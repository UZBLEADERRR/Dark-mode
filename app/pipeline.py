"""The render pipeline, split into two halves so scenes can be edited between.

    draft   topic -> script -> image prompts -> voice -> images   => status "review"
    render  captions -> scene clips -> cross-fade + mux           => status "done"

Between the two the user can rewrite a scene's narration, reword its image
prompt, change its camera move, or regenerate just that scene — without paying
for the whole video again. `auto_render` skips the checkpoint when you just want
the finished file.

Scene state lives in the job row, so an edit survives a page reload and the
render stage always works from what is actually on disk.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import traceback
import unicodedata
from pathlib import Path
from typing import Any, Callable

from . import config, pgstore, skills, store
from .providers import align, batch, images, storage, tts
from .render import overlays as ov
from .render import shots
from .render import subtitles as subs
from .render import video


class PipelineError(RuntimeError):
    pass


# ── small helpers ─────────────────────────────────────────────────────────────

def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _progress(job_id: str, step: str, percent: int, message: str | None = None,
              status: str = "running") -> None:
    store.update_job(job_id, status=status, step=step, progress=percent,
                     log=message or step)


def _note(job_id: str, message: str) -> None:
    """Add a line to the log without touching the step or the percentage.

    Retries used to happen in silence, so a slow provider looked exactly like a
    frozen app. Every note also refreshes `updated_at`, which is what the
    progress card watches to decide whether the job has genuinely stalled.
    """
    store.update_job(job_id, log=message)


def _retry_note(job_id: str, label: str) -> Callable[[int, Exception], None]:
    def note(attempt: int, exc: Exception) -> None:
        _note(job_id, f"{label} — attempt {attempt} failed, retrying ({_short(exc)})")

    return note


def _wait_note(job_id: str, label: str) -> Callable[[float, str], None]:
    """Report a deliberate wait, so pacing never looks like a hang.

    Only waits worth mentioning are logged. At ten calls a minute the limiter
    holds each line for about six seconds, and narrating that would bury the
    progress it is meant to sit beside.
    """
    def note(seconds: float, reason: str) -> None:
        if seconds <= 0:
            # Not a wait at all — something happened instead of waiting, which is
            # worth saying precisely because the log would otherwise be silent.
            _note(job_id, f"{label} — {reason}")
        elif seconds >= 10:
            _note(job_id, f"{label} — {reason}, waiting {seconds:.0f}s")

    return note


def _short(exc: Exception, limit: int = 120) -> str:
    text = " ".join(str(exc).split()) or exc.__class__.__name__
    return text[:limit] + "…" if len(text) > limit else text


def _slug(text: str, limit: int = 60) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return (slug or "video")[:limit]


async def _gather_limited(tasks: list[Any], limit: int) -> list[Any]:
    semaphore = asyncio.Semaphore(max(1, limit))

    async def guarded(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*(guarded(t) for t in tasks))


def workdir_for(job_id: str) -> Path:
    path = config.PROJECTS_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _materialize_heroes(workdir: Path, hero_ids: list[str]) -> dict[str, Path]:
    """Hero photos live in the database; image providers need real files."""
    folder = workdir / "heroes"
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for hero_id in hero_ids:
        blob = store.get_hero_image(hero_id)
        if blob is None:
            continue
        data, _mime, ext = blob
        target = folder / f"{hero_id}{ext or '.png'}"
        if not target.exists() or target.stat().st_size != len(data):
            target.write_bytes(data)
        paths[hero_id] = target
    return paths


def replace_scene_image(job_id: str, index: int, data: bytes) -> dict | None:
    """Drop a user-supplied still into a scene, in place of the generated one."""
    from PIL import Image

    job = store.get_job(job_id)
    if job is None:
        return None
    scenes = _load_scenes(job)
    scene = next((s for s in scenes if s["index"] == index), None)
    if scene is None:
        return None

    target = workdir_for(job_id) / "images" / f"scene_{scene['sid']}.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    # Normalise the same way generated stills are, so ffmpeg never meets a CMYK
    # or alpha-channel surprise halfway through a render.
    with Image.open(target) as img:
        img.convert("RGB").save(target, format="PNG")

    scene["image_path"] = str(target)
    scene["needs_image"] = False
    scene["image_version"] = int(scene.get("image_version", 0)) + 1
    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Scene {index + 1}: image replaced by upload")
    return scene


async def replace_scene_voice(job_id: str, index: int, data: bytes, suffix: str,
                              language: str = "") -> dict | None:
    """Use a recording of your own for one scene, in place of the generated one.

    This is the whole of "let me do the animal noises myself": you say the line,
    and the scene takes your timing rather than the synthesizer's. Everything
    downstream is measured from the file, so the picture, the captions and every
    scene after it move to fit what you actually said — the same way they would
    for a machine-read line.

    Kept in the pipeline rather than the HTTP layer because it is not a file
    swap: the duration has to be probed and the words re-aligned, or the
    captions would run to the length of a recording that no longer exists.
    """
    job = store.get_job(job_id)
    if job is None:
        return None
    scenes = _load_scenes(job)
    scene = next((s for s in scenes if s["index"] == index), None)
    if scene is None:
        return None

    workdir = workdir_for(job_id)
    # A new name each time. Browsers cache an audio URL hard, and a re-recorded
    # take that plays back as the old one is worse than no preview at all.
    take = int(scene.get("voice_version", 0)) + 1
    target = workdir / "audio" / f"scene_{scene['sid']}_take{take}{suffix or '.webm'}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)

    duration = await video.probe_duration(target)
    if duration <= 0.05:
        raise PipelineError("Yozuv bo'sh chiqdi — mikrofonni tekshirib qayta urinib ko'ring.")

    scene["audio_path"] = str(target)
    scene["audio_duration"] = duration + video.SCENE_GAP
    scene["voice_version"] = take
    scene["needs_voice"] = False
    # Own recording, own timings: the words are re-aligned against this take, so
    # the captions follow the voice rather than the text they were written from.
    scene["words"] = await align.words_for(
        audio_path=target, text=scene["narration"], duration=duration,
        provider=config.ALIGN_PROVIDER,
        language=language or job["request"].get("language", "en"),
        provider_words=[],
    )
    _recompute_starts(scenes)
    _save_scenes(job_id, scenes)
    await keep_media([scene], job_id)
    _note(job_id, f"Scene {index + 1}: o'z ovozingiz bilan almashtirildi")
    return scene


def _materialize_assets(workdir: Path, scenes: list[dict]) -> dict[str, Path]:
    """Overlay pictures live in the database; ffmpeg needs real files."""
    wanted = {
        layer.get("asset_id")
        for scene in scenes
        for layer in (scene.get("overlays") or [])
        if layer.get("type") == "image" and layer.get("asset_id")
    }
    if not wanted:
        return {}

    folder = workdir / "overlays"
    folder.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for asset_id in wanted:
        blob = store.get_asset(asset_id)
        if blob is None:
            continue
        data, _mime, ext = blob
        target = folder / f"{asset_id}{ext or '.png'}"
        if not target.exists() or target.stat().st_size != len(data):
            target.write_bytes(data)
        paths[asset_id] = target
    return paths


def _materialize_music(workdir: Path, music_id: str | None) -> Path | None:
    if not music_id:
        return None
    blob = store.get_music_audio(music_id)
    if blob is None:
        return None
    data, _mime, ext = blob
    target = workdir / f"music{ext or '.mp3'}"
    target.write_bytes(data)
    return target


def _materialize_sfx(workdir: Path, scenes: list[dict]) -> list[dict]:
    """Cue every scene's sting at its place on the finished timeline."""
    folder = workdir / "sfx"
    cache: dict[str, Path | None] = {}
    cues: list[dict] = []

    for scene in scenes:
        sfx_id = scene.get("sfx_id")
        if not sfx_id:
            continue
        if sfx_id not in cache:
            blob = store.get_music_audio(sfx_id)
            if blob is None:
                cache[sfx_id] = None
            else:
                data, _mime, ext = blob
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"{sfx_id}{ext or '.mp3'}"
                if not target.exists() or target.stat().st_size != len(data):
                    target.write_bytes(data)
                cache[sfx_id] = target
        path = cache[sfx_id]
        if path is None:
            continue
        duration = float(scene.get("audio_duration") or 0.0)
        offset = max(0.0, min(float(scene.get("sfx_offset") or 0.0), max(0.0, duration - 0.1)))
        cues.append({
            "path": path,
            "at": float(scene.get("start", 0.0)) + offset,
            "volume": float(scene.get("sfx_volume") or 1.0),
        })
    return cues


BRAND_KEY = "brand"

DEFAULT_BRAND: dict = {
    "accent": "#FF3B30",
    "logo_asset_id": "",
    "logo_x": 0.9,
    "logo_y": 0.1,
    "logo_size": 0.11,
    "logo_opacity": 0.9,
    "art_style": "",
    "tone": "",
    "voice_id": "",
    "tts_provider": "",
    "music_id": "",
    "caption_style": None,
}


def brand() -> dict:
    stored = store.get_setting(BRAND_KEY) or {}
    return {**DEFAULT_BRAND, **(stored if isinstance(stored, dict) else {})}


def _apply_brand(scenes: list[dict], request: dict, hook: str) -> None:
    """Stamp the logo on every scene and the hook on the first one.

    Both are ordinary overlay layers, so once they are there they behave like
    anything the user added by hand — draggable, restyleable, deletable.
    """
    kit = brand()

    if request.get("brand_logo", True) and kit.get("logo_asset_id") \
            and store.get_asset(kit["logo_asset_id"]) is not None:
        logo = ov.normalize({
            "id": "brandlogo", "type": "image", "asset_id": kit["logo_asset_id"],
            "x": kit.get("logo_x", 0.9), "y": kit.get("logo_y", 0.1),
            "size": kit.get("logo_size", 0.11), "opacity": kit.get("logo_opacity", 0.9),
            "anim": "none", "start": 0, "end": 0,
        }, 0)
        if logo is not None:
            for scene in scenes:
                scene["overlays"] = [logo, *(scene.get("overlays") or [])]

    # The first three seconds decide whether a short is watched at all, so the
    # line the Director wrote as the hook goes on the frame, not just in the
    # voice-over.
    if request.get("auto_hook") and hook.strip() and scenes:
        text = hook.strip()
        if len(text) > 60:
            text = text[:57].rsplit(" ", 1)[0] + "…"
        layer = ov.normalize({
            "id": "brandhook", "type": "text", "text": text.upper(),
            "x": 0.5, "y": 0.17, "size": 0.068, "colour": "#FFFFFF",
            "box": True, "box_colour": kit.get("accent") or "#FF3B30",
            "box_opacity": 0.94, "anim": "pop", "start": 0, "end": 3.0,
        }, 0)
        if layer is not None:
            scenes[0]["overlays"] = [*(scenes[0].get("overlays") or []), layer]


def _ensure_sids(scenes: list[dict]) -> list[dict]:
    """Give every scene a file-name key that does not move when it does.

    Assets used to be named from the scene's position, which was fine until
    scenes could be reordered — then scene 3's regenerated image would land on
    top of scene 1's file. A scene keeps its `sid` for life instead. Jobs made
    before this existed get the sid their existing files already imply.
    """
    used: set[str] = set()
    for i, scene in enumerate(scenes):
        sid = str(scene.get("sid") or "")
        if not sid or sid in used:
            sid = f"{i:03d}" if f"{i:03d}" not in used else store.new_id("sc")[3:]
            scene["sid"] = sid
        used.add(sid)
        # One place where the shape is completed, so nothing downstream has to
        # guess whether a scene from an older job has a layer list.
        scene.setdefault("overlays", [])
        scene.setdefault("shots", [])
        # Shots are named from the scene, so a scene that moves takes its
        # pictures with it and never lands on another scene's files.
        for j, shot in enumerate(scene["shots"]):
            if not shot.get("sid"):
                shot["sid"] = f"{sid}-{j}"
        seen: set[str] = set()
        for shot in scene["shots"]:
            while shot["sid"] in seen:
                shot["sid"] = f"{shot['sid']}x"
            seen.add(shot["sid"])
    return scenes


def _reindex(scenes: list[dict]) -> list[dict]:
    for i, scene in enumerate(scenes):
        scene["index"] = i
    _recompute_starts(scenes)
    return scenes


def _file_url(path: str | None, version: object = None) -> str | None:
    """A project file's public URL, derived from where the file actually is."""
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(config.PROJECTS_DIR.resolve())
    except (ValueError, OSError):
        return None
    url = f"/api/files/{relative.as_posix()}"
    return f"{url}?v={version}" if version is not None else url


def _recompute_starts(scenes: list[dict]) -> float:
    cursor = 0.0
    for scene in scenes:
        scene["start"] = cursor
        cursor += float(scene.get("audio_duration") or 0.0)
    return cursor


def _save_scenes(job_id: str, scenes: list[dict], **extra: Any) -> None:
    store.update_job(job_id, result={"scenes": scenes, **extra})


def _checkpoint(job_id: str, scenes: list[dict]) -> None:
    """Write the scene list down the moment one more piece of it is finished.

    Saving only at the end of a stage was costing two different things. A crash
    at scene forty threw away thirty-nine pictures that were sitting on disk,
    because nothing in the database pointed at them. And the browser had nothing
    to show while it waited — a fifty-scene draft was a progress bar and a
    promise. One row write per finished asset buys both: the work is banked, and
    it appears as it is made.
    """
    store.update_job(job_id, result={"scenes": scenes})


def _media_paths(scenes: list[dict]) -> list[Path]:
    out: list[Path] = []
    for scene in scenes:
        for holder in [scene] + list(scene.get("shots") or []):
            for key in ("image_path", "audio_path"):
                if holder.get(key):
                    out.append(Path(holder[key]))
    return out


# A single file this big is not worth keeping in a database row. Nothing the
# renderer makes comes close — a scene picture is about a megabyte and a voice
# clip far less — so in practice this only ever catches something that has gone
# wrong.
MAX_KEPT_BYTES = 12 * 1024 * 1024


def _keep_in_db(job_id: str, paths: list[Path]) -> int:
    """Keep the render's files in the database, when there is nowhere better.

    Object storage is the right home for a megabyte of picture and it is used
    whenever it is configured. But most people will attach a database and stop
    there, and then "everything is saved" has to still be true — otherwise the
    first redeploy quietly bins every picture and voice clip a project paid for,
    which is exactly what happened.
    """
    already = store.stored_media(job_id)
    kept = 0
    for local in paths:
        key = storage.key_for(local, config.PROJECTS_DIR)
        if not key or key in already or not local.exists():
            continue
        try:
            data = local.read_bytes()
        except OSError:
            continue
        if len(data) > MAX_KEPT_BYTES:
            continue
        store.put_media(job_id, key, data)
        kept += 1
    return kept


def _kept_note(job_id: str, scenes: list[dict], kept: int) -> None:
    """Say so when work that was paid for was not saved anywhere.

    This used to be counted and thrown away. A project whose files were all lost
    looked exactly like one whose files were all kept, right up until the
    redeploy — and by then the only way to find out was to pay for it twice.
    """
    wanted = sum(1 for p in _media_paths(scenes) if p.exists())
    if not wanted or kept >= wanted:
        return
    where = "Supabase Storage" if storage.backend() == "supabase" else "baza"
    _note(job_id, f"DIQQAT: {wanted - kept}/{wanted} fayl saqlanmadi "
                  f"({where} qabul qilmadi) — deploydan keyin ular yo'qoladi.")


async def keep_media(scenes: list[dict], job_id: str = "") -> int:
    """Put every picture and voice clip made so far somewhere it will survive.

    Called between stages rather than after each file, so a fifty-scene project
    pays one batch instead of fifty round trips inside the generation loop. What
    it buys is the thing that used to hurt most: a render that dies at scene
    thirty comes back with thirty pictures, not with a scene list pointing at
    files that no longer exist.
    """
    kept = 0
    unsent = list(_media_paths(scenes))

    if storage.backend() == "supabase":
        targets = [(local, storage.key_for(local, config.PROJECTS_DIR))
                   for local in unsent]
        results = await _gather_limited(
            [storage.mirror(local, key) for local, key in targets if key], 4)
        sent = {local for (local, key), ok in zip(
            [t for t in targets if t[1]], results) if ok}
        kept = len(sent)
        # Whatever the bucket would not take is not simply lost. A misconfigured
        # bucket used to swallow every file in silence — the upload failed, the
        # database branch never ran because a bucket was "configured", and the
        # first redeploy took a project's entire voice-over with it.
        unsent = [local for local in unsent if local not in sent]

    if unsent and job_id and pgstore.enabled():
        kept += await asyncio.to_thread(_keep_in_db, job_id, unsent)
    return kept


def forget_missing(scenes: list[dict]) -> int:
    """Drop every path that points at a file which is not there.

    A row saying `audio_path` and a disk saying nothing is not progress — it is
    the render walking into ffmpeg with a filename that cannot be opened, which
    is what "Error opening input file .../audio/scene_x" is. Cleared here, the
    stage that follows simply remakes what is missing, which is what it would
    have done had the row been honest.

    A container replaced between the draft and the render is the ordinary way
    this happens, and it is ordinary precisely when the files were never
    mirrored anywhere.
    """
    lost = 0
    for scene in scenes:
        for holder in [scene] + list(scene.get("shots") or []):
            if holder.get("image_path") and not Path(holder["image_path"]).exists():
                holder["image_path"] = None
                holder["needs_image"] = True
                lost += 1
        if scene.get("audio_path") and not Path(scene["audio_path"]).exists():
            scene["audio_path"] = None
            scene["words"] = []
            scene["needs_voice"] = True
            lost += 1
    return lost


def _restore_from_db(scenes: list[dict]) -> int:
    brought = 0
    for local in _media_paths(scenes):
        if local.exists():
            continue
        key = storage.key_for(local, config.PROJECTS_DIR)
        data = store.get_media(key) if key else None
        if data is None:
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        brought += 1
    return brought


async def restore_media(scenes: list[dict]) -> int:
    """Bring back the pictures and voice clips a redeploy took with it.

    The scene list survives in the database; the files it points at were on a
    disk that no longer exists. Anything already present is left alone, so this
    costs nothing on the normal path and is the difference between "press Render
    and it finishes" and "press Render and it draws all fifty scenes again".
    """
    brought = 0
    if storage.backend() == "supabase":
        wanted = [(local, storage.key_for(local, config.PROJECTS_DIR))
                  for local in _media_paths(scenes) if not local.exists()]
        pairs = [(local, key) for local, key in wanted if key]
        results = await _gather_limited(
            [storage.fetch(key, local) for local, key in pairs], 4)
        brought = sum(1 for got in results if got is True)

    # Whatever the bucket did not have. The two halves have to match: a file
    # the bucket refused was kept in the database, and looking only in the
    # bucket would leave it there unreachable — which is the same loss again,
    # arrived at from the other direction.
    if pgstore.enabled():
        brought += await asyncio.to_thread(_restore_from_db, scenes)
    return brought


def _load_scenes(job: dict) -> list[dict]:
    return _ensure_sids(job.get("result", {}).get("scenes") or [])


def public_scene(job_id: str, scene: dict) -> dict:
    """The shape the browser sees — editable fields plus preview media."""
    return {
        "index": scene["index"],
        "sid": scene.get("sid", ""),
        "narration": scene.get("narration", ""),
        "image_prompt": scene.get("image_prompt", ""),
        "motion": scene.get("motion", "zoom_in"),
        "motion_strength": round(float(scene.get("motion_strength") or 1.0), 2),
        "transition": scene.get("transition") or "",
        "on_screen_text": scene.get("on_screen_text", ""),
        "hero_ids": scene.get("hero_ids", []),
        "speaker": scene.get("speaker") or "",
        "overlays": scene.get("overlays") or [],
        "sfx_id": scene.get("sfx_id") or "",
        "sfx_volume": round(float(scene.get("sfx_volume") or 1.0), 2),
        "sfx_offset": round(float(scene.get("sfx_offset") or 0.0), 2),
        "start": round(float(scene.get("start", 0.0)), 2),
        "duration": round(float(scene.get("audio_duration", 0.0)), 2),
        "image_url": _file_url(scene.get("image_path"), scene.get("image_version", 0)),
        # The editor plays this to preview the scene with its captions and
        # layers running in step, which is why the word timings ride along.
        # Versioned like the picture is: a re-recorded take must not play back
        # as the one the browser already cached.
        "audio_url": _file_url(scene.get("audio_path"), scene.get("voice_version", 0)),
        "words": scene.get("words") or [],
        "needs_image": bool(scene.get("needs_image")),
        "needs_voice": bool(scene.get("needs_voice")),
        # Always the real list, so an unsplit scene reports no shots rather than
        # a phantom one the editor would then offer to delete.
        "shots": [public_shot(job_id, scene, s, i)
                  for i, s in enumerate(scene.get("shots") or [])],
    }


def public_shot(job_id: str, scene: dict, shot: dict, position: int) -> dict:
    total = sum(max(0.25, float(s.get("weight") or 1.0)) for s in scene.get("shots") or [shot])
    duration = float(scene.get("audio_duration") or 0.0)
    weight = max(0.25, float(shot.get("weight") or 1.0))
    return {
        "sid": shot.get("sid", ""),
        "position": position,
        "prompt": shot.get("prompt", ""),
        "motion": shot.get("motion", "zoom_in"),
        "motion_strength": round(float(shot.get("motion_strength") or 1.0), 2),
        "transition": shot.get("transition") or "",
        "weight": round(weight, 2),
        # What this shot will actually be on screen for — the number the user is
        # really choosing when they drag its share.
        "seconds": round(duration * weight / total, 2) if total else 0.0,
        "image_url": _file_url(shot.get("image_path"), shot.get("image_version", 0)),
        "needs_image": bool(shot.get("needs_image") or not shot.get("image_path")),
    }


# ── who says the line ─────────────────────────────────────────────────────────

def cast_voices(hero_ids: list[str] | None = None) -> dict[str, dict[str, str]]:
    """Every character that has been given a voice, keyed by hero id.

    Looked up once per stage rather than per scene: a fifty-scene cartoon with
    four characters would otherwise make fifty database round trips to learn the
    same four answers.
    """
    heroes = store.list_heroes() if hero_ids is None else store.get_heroes(hero_ids)
    return {
        h["id"]: {"voice_id": h.get("voice_id") or "",
                  "provider": (h.get("tts_provider") or "").lower(),
                  "name": h.get("name", "")}
        for h in heroes
        if h.get("voice_id")
    }


def voice_for(scene: dict, cast: dict[str, dict[str, str]],
              default_provider: str, default_voice: str | None) -> tuple[str, str | None, str]:
    """Which provider and voice read this scene, and whose voice it is.

    A scene names its speaker; a speaker with a voice of its own uses it. Anything
    else — no speaker, an unknown one, a character nobody gave a voice — is the
    narrator, which is exactly how every video worked before characters could
    speak. A character with a voice but no provider of its own borrows the
    project's, so picking an ElevenLabs voice does not also mean re-picking
    ElevenLabs on every character.
    """
    speaker = str(scene.get("speaker") or "")
    entry = cast.get(speaker)
    if not entry:
        return default_provider, default_voice, ""
    return entry["provider"] or default_provider, entry["voice_id"], entry["name"]


# ── cartoon staging ───────────────────────────────────────────────────────────

async def _draw_actor(hero: dict, pose: str, workdir: Path, provider: str,
                      job_id: str) -> Path | None:
    """Draw one character in one pose, full length, and lift it off its background.

    Per pose rather than per scene: a character that stands and talks through six
    scenes is drawn once for all six, and drawn again the moment it is frightened
    or running. That is what makes a scene look staged instead of showing the same
    smiling figure under every line, without paying for a picture per scene.
    """
    folder = workdir / "cutouts"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{hero['id']}-{abs(hash(pose)) % 10**8:08d}"
    source = folder / f"{stem}-src.png"
    keyed = folder / f"{stem}.png"

    refs: list[Path] = []
    blob = store.get_hero_image(hero["id"])
    if blob is not None:
        ref = folder / f"{hero['id']}-ref{blob[2] or '.png'}"
        if not ref.exists():
            ref.write_bytes(blob[0])
        refs = [ref]

    who = f"{hero.get('name', 'the character')}, {hero.get('description', '')}".strip(", ")
    path, warning = await images.generate_image(
        prompt=f"{who}. {pose}. {images.CUTOUT_INSTRUCTION}",
        negative_prompt=images.CUTOUT_NEGATIVE,
        reference_paths=refs, aspect="1:1", size=(1024, 1024),
        provider=provider, out_path=source, attempts=2,
        on_retry=_retry_note(job_id, f"{hero.get('name', 'Actor')} cut-out"),
    )
    if warning:
        return None
    try:
        await video.cut_out(path, keyed)
    except (video.RenderError, OSError):
        return None
    if not await video.has_alpha(keyed):
        # Nothing came away, or everything did. Either way what is left is not a
        # cut-out, and laying it over a background would paste a rectangle onto
        # the scene — which is exactly what used to happen.
        return None
    return keyed


async def stage_cartoon(job_id: str, scenes: list[dict], heroes: list[dict], *,
                        workdir: Path, provider: str, action: str, language: str,
                        video_format: str) -> list[str]:
    """Stage every scene, draw its cast, and hang the actors on it.

    Two things change per scene. The image prompt becomes a *background* prompt
    with no characters in it and no character references attached — a hero photo
    handed to the background generator comes back as the character painted into
    the scenery, or worse, as the reference sheet itself reproduced whole. And an
    overlay layer is added per actor, carrying the move it was given.
    """
    warnings: list[str] = []
    if not heroes:
        return ["Multfilm rejimi uchun kamida bitta qahramon kerak."]

    _progress(job_id, "staging", 30, "Sahnalar sahnalashtirilmoqda")
    staged = await skills.stage_scenes(
        scenes=scenes, heroes=heroes, action=action, language=language,
        video_format=video_format,
    )
    if not staged:
        return ["Sahnalashtirish natija bermadi — oddiy rejimda davom etildi."]

    # Every distinct (character, pose) in the whole project, drawn once.
    wanted: dict[tuple[str, str], None] = {}
    for plan in staged:
        for actor in plan.get("actors") or []:
            wanted[(actor["hero_id"], actor["pose"])] = None
    if not wanted:
        return ["Sahnalarga aktyor joylanmadi."]

    by_id = {h["id"]: h for h in heroes}
    _progress(job_id, "cast", 34,
              f"{len(wanted)} ta aktyor holati chizilmoqda")
    drawn = await _gather_limited(
        [_draw_actor(by_id[hero_id], pose, workdir, provider, job_id)
         for hero_id, pose in wanted], 2)

    cutouts: dict[tuple[str, str], str] = {}
    lost: set[str] = set()
    for (hero_id, pose), path in zip(wanted, drawn):
        if path is None:
            lost.add(hero_id)
            continue
        asset = store.add_asset(by_id[hero_id].get("name") or "Aktyor",
                                path.read_bytes(), "image/png", ".png")
        cutouts[(hero_id, pose)] = asset["id"]

    for hero_id in lost:
        if not any(key[0] == hero_id for key in cutouts):
            warnings.append(
                f"{by_id[hero_id].get('name', 'Qahramon')} fondan ajratilmadi — "
                "u sahnada harakatlanmaydi.")
    if not cutouts:
        return warnings + ["Hech bir qahramon kesib olinmadi — oddiy rejimda davom etildi."]

    placed = 0
    for scene, plan in zip(scenes, staged):
        actors = [a for a in (plan.get("actors") or [])
                  if (a["hero_id"], a["pose"]) in cutouts]
        if plan.get("background"):
            scene["image_prompt"] = plan["background"]
            scene["negative_prompt"] = (
                (scene.get("negative_prompt", "") + ", " if scene.get("negative_prompt") else "")
                + "people, person, character, figure, human, silhouette, crowd")
            scene["needs_image"] = True
            for shot in scene.get("shots") or []:
                shot["prompt"] = plan["background"]
                shot["needs_image"] = True
            # The reference photos go with them. This is the difference between a
            # jungle and a picture of the mascot's brand sheet.
            scene["hero_ids"] = []

        if actors:
            # The background holds still under a moving character. A slow zoom is
            # what sells a still picture as a shot; under a walking cut-out it
            # fights the movement, and the character reads as sliding on glass.
            scene["motion"] = "still"
            for shot in scene.get("shots") or []:
                shot["motion"] = "still"

        layers = [l for l in (scene.get("overlays") or []) if not l.get("actor_of")]
        for i, actor in enumerate(actors):
            layers.append({
                "id": f"act{scene['index']}_{i}",
                "type": "image",
                "asset_id": cutouts[(actor["hero_id"], actor["pose"])],
                "x": actor["x"], "y": actor["y"], "size": actor["size"],
                "start": actor["enters_at"], "end": 0.0,   # 0 = to the scene's end
                "anim": actor["move"], "opacity": 1.0, "rotate": 0.0,
                # Marks the layer as the Choreographer's, so re-staging replaces
                # its own work and leaves anything the user added alone.
                "actor_of": actor["hero_id"],
            })
            placed += 1
        scene["overlays"] = ov.normalize_all(layers)

    if placed:
        _note(job_id, f"{placed} ta aktyor sahnalarga joylandi "
                      f"({len(cutouts)} xil holatdan)")
    else:
        warnings.append("Sahnalarga aktyor joylanmadi.")
    return warnings


# ── stages ────────────────────────────────────────────────────────────────────

async def _voice_scenes(
    *,
    scenes: list[dict],
    targets: list[dict],
    workdir: Path,
    provider: str,
    voice_id: str | None,
    language: str,
    job_id: str,
    base_progress: int = 25,
    span: int = 20,
    strict: bool = True,
) -> list[str]:
    """Synthesize `targets` (a subset of `scenes`) and refresh every start time.

    With `strict=False` a scene that will not speak is left marked `needs_voice`
    instead of failing the batch. That is what the draft wants: losing fifty-seven
    good scenes because the provider went quiet on the fifty-eighth is a far worse
    outcome than finishing with one gap the render stage can try again.
    """
    audio_dir = workdir / "audio"
    warnings: list[str] = []
    done = 0
    lock = asyncio.Lock()
    cast = cast_voices()

    async def one(scene: dict) -> None:
        nonlocal done
        say_with, say_as, who = voice_for(scene, cast, provider, voice_id)
        label = f"Scene {scene['index'] + 1}" + (f" ({who})" if who else "")
        try:
            path, provider_words = await tts.synthesize(
                text=scene["narration"],
                out_path=audio_dir / f"scene_{scene['sid']}",
                provider=say_with,
                voice_id=say_as,
                on_retry=_retry_note(job_id, f"{label} voice-over"),
                on_wait=_wait_note(job_id, f"{label} voice-over"),
            )
            raw_duration = await video.probe_duration(path)
            words = await align.words_for(
                audio_path=path,
                text=scene["narration"],
                duration=raw_duration,
                provider=config.resolve_align_provider(say_with),
                language=language,
                provider_words=provider_words,
            )
        except Exception as exc:  # noqa: BLE001 - re-raised below when strict
            if strict:
                raise
            scene["needs_voice"] = True
            async with lock:
                done += 1
                warnings.append(f"{label} has no voice-over yet: {_short(exc)}")
                _note(job_id, f"{label} voice-over failed — carrying on")
            return

        scene["audio_path"] = str(path)
        scene["audio_duration"] = raw_duration + video.SCENE_GAP
        scene["words"] = words
        scene["needs_voice"] = False
        async with lock:
            done += 1
            _checkpoint(job_id, scenes)
            _progress(job_id, "voice", base_progress + int(span * done / max(len(targets), 1)),
                      f"Voice-over {done}/{len(targets)}")

    async def finish(scene: dict, path: Path, provider_words: list[dict],
                     said_with: str) -> None:
        """Turn a finished recording into the timings the timeline needs."""
        nonlocal done
        raw_duration = await video.probe_duration(path)
        scene["audio_path"] = str(path)
        scene["audio_duration"] = raw_duration + video.SCENE_GAP
        scene["words"] = await align.words_for(
            audio_path=path, text=scene["narration"], duration=raw_duration,
            provider=config.resolve_align_provider(said_with), language=language,
            provider_words=provider_words,
        )
        scene["needs_voice"] = False
        async with lock:
            done += 1
            _checkpoint(job_id, scenes)
            _progress(job_id, "voice", base_progress + int(span * done / max(len(targets), 1)),
                      f"Voice-over {done}/{len(targets)}")

    async def read_together(group: list[dict], said_with: str, said_as: str | None) -> None:
        """Read one voice's lines as passages instead of one at a time."""
        _note(job_id, f"Reading {len(group)} lines in "
                      f"{len(tts.batches([g['narration'] for g in group], max_chars=config.TTS_BATCH_CHARS, max_lines=config.TTS_BATCH_LINES))} passage(s)")
        try:
            spoken = await tts.synthesize_many(
                lines=[g["narration"] for g in group],
                out_paths=[audio_dir / f"scene_{g['sid']}" for g in group],
                provider=said_with, voice_id=said_as,
                on_retry=_retry_note(job_id, "Voice-over"),
                on_wait=_wait_note(job_id, "Voice-over"),
            )
        except Exception as exc:  # noqa: BLE001 - handled per scene below
            if strict:
                raise
            spoken = []
            warnings.append(f"Voice-over failed: {_short(exc)}")

        for scene, result in zip(group, spoken):
            await finish(scene, *result, said_with)
        for scene in group[len(spoken):]:
            scene["needs_voice"] = True
            warnings.append(f"Scene {scene['index'] + 1} has no voice-over yet")

    # Batching reads several lines in one request, so it can only ever group
    # lines that are read by the same voice. A cartoon with four characters is
    # therefore four passages rather than one — still far fewer requests than
    # one per scene, and nobody ends up speaking in somebody else's voice.
    groups: dict[tuple[str, str | None], list[dict]] = {}
    alone: list[dict] = []
    for scene in targets:
        say_with, say_as, _who = voice_for(scene, cast, provider, voice_id)
        if tts.can_batch(say_with):
            groups.setdefault((say_with, say_as), []).append(scene)
        else:
            alone.append(scene)

    for (say_with, say_as), group in groups.items():
        if len(group) > 1:
            await read_together(group, say_with, say_as)
        else:
            alone.extend(group)

    if alone:
        await _gather_limited([one(scene) for scene in alone], config.TTS_CONCURRENCY)
    _recompute_starts(scenes)
    return warnings


def _picture_work(targets: list[dict], only_stale: bool = False,
                  missing_only: bool = False) -> list[tuple[dict, dict | None]]:
    """Every picture that needs drawing, as (scene, shot) — shot None if unsplit.

    `only_stale` covers both senses of "needs drawing": never made, and made
    before an edit. `missing_only` narrows it to the first, which is what
    carrying on from a stopped run means — redrawing an edited scene is what
    Render is for, and doing it inside a resume would spend money the user did
    not ask to spend.
    """
    work: list[tuple[dict, dict | None]] = []
    for scene in targets:
        holders = scene.get("shots") or []
        if not holders:
            if _needs_drawing(scene, only_stale, missing_only):
                work.append((scene, None))
            continue
        for shot in holders:
            # Re-rendering after an edit should redraw the shot that changed,
            # not every shot in the scene it happens to sit in.
            if _needs_drawing(shot, only_stale, missing_only):
                work.append((scene, shot))
    return work


def _needs_drawing(holder: dict, only_stale: bool, missing_only: bool) -> bool:
    if missing_only:
        return not holder.get("image_path")
    if not only_stale:
        return True
    return bool(holder.get("needs_image")) or not holder.get("image_path")


def _wants_picture(scene: dict) -> bool:
    return bool(_picture_work([scene], only_stale=True))


async def _render_images(
    *,
    scenes: list[dict],
    targets: list[dict],
    workdir: Path,
    hero_paths: dict[str, Path],
    provider: str,
    aspect: str,
    size: tuple[int, int],
    job_id: str,
    base_progress: int = 48,
    span: int = 24,
    only_stale: bool = False,
    missing_only: bool = False,
    batch_wanted: bool = False,
    batch_patience_minutes: float = 0.0,
) -> list[str]:
    image_dir = workdir / "images"
    warnings: list[str] = []
    done = 0
    lock = asyncio.Lock()
    # A split scene needs a picture per shot, not per scene, so the unit of work
    # here is a shot. `None` stands for a scene that was never split and keeps
    # its picture on the scene itself.
    work = _picture_work(targets, only_stale=only_stale, missing_only=missing_only)
    total = max(len(work), 1)

    async def one(scene: dict, shot: dict | None) -> None:
        nonlocal done
        refs = [hero_paths[h] for h in scene.get("hero_ids", []) if h in hero_paths]
        holder = shot if shot is not None else scene
        label = f"Scene {scene['index'] + 1}"
        if shot is not None and len(scene.get("shots") or []) > 1:
            label += f" shot {scene['shots'].index(shot) + 1}"

        path, warning = await images.generate_image(
            prompt=holder.get("prompt") or holder.get("image_prompt") or scene["image_prompt"],
            negative_prompt=holder.get("negative_prompt") or scene.get("negative_prompt", ""),
            reference_paths=refs,
            aspect=aspect,
            size=size,
            provider=provider,
            out_path=image_dir / (f"shot_{shot['sid']}.png" if shot is not None
                                  else f"scene_{scene['sid']}.png"),
            on_retry=_retry_note(job_id, f"{label} image"),
        )
        holder["image_path"] = str(path)
        holder["needs_image"] = False
        holder["image_version"] = int(holder.get("image_version", 0)) + 1
        if shot is not None:
            # The scene's own thumbnail follows its first shot, so the filmstrip
            # and the editor keep showing something recognisable.
            scene["needs_image"] = any(s.get("needs_image") for s in scene["shots"])
            if scene["shots"][0] is shot:
                scene["image_path"] = str(path)
                scene["image_version"] = int(scene.get("image_version", 0)) + 1
        async with lock:
            done += 1
            if warning:
                warnings.append(f"{label}: {warning}")
            _checkpoint(job_id, scenes)
            _progress(job_id, "images", base_progress + int(span * done / total),
                      f"Scene image {done}/{total}")

    if work and batch_wanted:
        # Half the price, and there is time to wait for it. Whatever the batch
        # does not return is made the ordinary way below — a scene without a
        # picture is not an acceptable saving.
        got = await _batch_images(
            work=work, image_dir=image_dir, hero_paths=hero_paths, aspect=aspect,
            job_id=job_id, base_progress=base_progress, span=span,
            patience_minutes=batch_patience_minutes)
        for scene, shot in list(work):
            key = _work_key(scene, shot)
            path = got.get(key)
            if path is None:
                continue
            _adopt_picture(scene, shot, path)
            done += 1
        work = [(s, sh) for s, sh in work if _work_key(s, sh) not in got]
        if got:
            _note(job_id, f"Batch: {len(got)}/{total} rasm arzon narxda tayyorlandi")

    if work:
        await _gather_limited([one(s, sh) for s, sh in work], config.IMAGE_CONCURRENCY)
    return warnings


def _work_key(scene: dict, shot: dict | None) -> str:
    return f"shot:{shot['sid']}" if shot is not None else f"scene:{scene['sid']}"


def _adopt_picture(scene: dict, shot: dict | None, path: Path) -> None:
    """Attach a finished picture, from wherever it came from."""
    holder = shot if shot is not None else scene
    holder["image_path"] = str(path)
    holder["needs_image"] = False
    holder["image_version"] = int(holder.get("image_version", 0)) + 1
    if shot is not None:
        scene["needs_image"] = any(s.get("needs_image") for s in scene["shots"])
        if scene["shots"][0] is shot:
            scene["image_path"] = str(path)
            scene["image_version"] = int(scene.get("image_version", 0)) + 1


async def _batch_images(
    *, work: list[tuple[dict, dict | None]], image_dir: Path,
    hero_paths: dict[str, Path], aspect: str, job_id: str,
    base_progress: int, span: int, patience_minutes: float = 0.0,
) -> dict[str, Path]:
    """Draw as many of these as the batch API will, and say which it did.

    Never raises: every failure here means "pay full price for this one", which is
    the ordinary path and not an error worth stopping a render for.
    """
    if not batch.available():
        _note(job_id, "Batch so'raldi, ammo Gemini kaliti yo'q — oddiy yo'l bilan")
        return {}

    model = config.model("gemini_image")
    items = []
    for scene, shot in work:
        holder = shot if shot is not None else scene
        refs = [hero_paths[h] for h in scene.get("hero_ids", []) if h in hero_paths]
        pictures = []
        for ref in refs[:3]:
            try:
                pictures.append((ref.read_bytes(), "image/png"))
            except OSError:
                continue
        prompt = (holder.get("prompt") or holder.get("image_prompt")
                  or scene.get("image_prompt") or "")
        items.append({
            "key": _work_key(scene, shot),
            "request": batch.image_request(
                model, f"{prompt}\n\nAspect ratio: {aspect}.",
                negative=holder.get("negative_prompt") or scene.get("negative_prompt", ""),
                images=pictures),
        })

    try:
        name = await batch.submit(model, items, label=f"sarideo {job_id}")
    except batch.BatchError as exc:
        _note(job_id, f"Batch boshlanmadi ({exc}) — oddiy yo'l bilan")
        return {}

    _progress(job_id, "images", base_progress,
              f"Batch yuborildi — {len(items)} rasm arzon narxda kutilmoqda")

    look = await batch.gather(
        name,
        # How long it is worth waiting is not a property of the batch API, it is a
        # property of when the video is due — so the caller says.
        patience=(patience_minutes * 60) if patience_minutes > 0 else None,
        on_wait=lambda waited, state: _progress(
            job_id, "images", base_progress,
            f"Batch kutilmoqda — {int(waited // 60)} daqiqa ({state})"))

    if look.get("why"):
        _note(job_id, f"Batch: {look['why']} — qolgani oddiy yo'l bilan")

    made: dict[str, Path] = {}
    image_dir.mkdir(parents=True, exist_ok=True)
    for key, response in (look.get("results") or {}).items():
        data = batch.image_bytes(response)
        if not data:
            continue
        target = image_dir / (
            f"shot_{key.split(':', 1)[1]}.png" if key.startswith("shot:")
            else f"scene_{key.split(':', 1)[1]}.png")
        try:
            target.write_bytes(data)
        except OSError:
            continue
        made[key] = target
    for key, why in (look.get("errors") or {}).items():
        _note(job_id, f"Batch rad etdi ({key}): {why} — oddiy yo'l bilan")
    return made


def _split_uploaded_audio(scenes: list[dict], words: list[dict], total: float) -> None:
    """Give each scene its slice of a voice-over the user supplied.

    The Director copies the transcript verbatim, so consuming one timed word per
    narration word walks both lists in lockstep. Scene boundaries then tile the
    whole file, which keeps the images covering every second of audio even if a
    word or two failed to align.
    """
    cursor = 0
    slices: list[list[dict]] = []
    for scene in scenes:
        count = len(_tokens(scene["narration"]))
        slices.append(words[cursor : cursor + count])
        cursor += count

    starts: list[float] = []
    previous = 0.0
    for i, slice_ in enumerate(slices):
        start = 0.0 if i == 0 else (float(slice_[0]["start"]) if slice_ else previous)
        starts.append(max(start, previous))
        previous = starts[-1]

    for i, scene in enumerate(scenes):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(scenes) else total
        scene["start"] = start
        scene["audio_duration"] = max(0.6, end - start)
        scene["words"] = [
            {"text": w["text"],
             "start": max(0.0, float(w["start"]) - start),
             "end": max(0.0, float(w["end"]) - start)}
            for w in slices[i]
        ]


# ── stage 1: draft ────────────────────────────────────────────────────────────

async def run_draft(job_id: str) -> None:
    """Script, voice and images. Stops at `review` unless auto_render is set."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = []

    try:
        if not video.ffmpeg_available():
            raise PipelineError("ffmpeg is not installed in this container.")

        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        width, height = fmt["width"], fmt["height"]
        language = request.get("language", "en")
        heroes = store.get_heroes(request.get("hero_ids") or [])
        hero_paths = _materialize_heroes(workdir, [h["id"] for h in heroes])

        image_provider = (request.get("image_provider") or config.IMAGE_PROVIDER).lower()
        if not config.image_provider_ready(image_provider):
            raise PipelineError(f"The '{image_provider}' image provider has no API key configured.")

        uploaded_audio = request.get("narration_audio")
        tts_provider = (request.get("tts_provider") or config.TTS_PROVIDER).lower()

        # --- script ---------------------------------------------------------
        if uploaded_audio:
            _progress(job_id, "transcribe", 6, "Transcribing the uploaded voice-over")
            source_audio = Path(uploaded_audio)
            if not source_audio.exists():
                raise PipelineError("The uploaded narration file is missing.")
            transcript = await align.transcribe_full(source_audio, language)
            if not transcript["text"]:
                raise PipelineError(
                    "Could not transcribe the uploaded audio. An OPENAI_API_KEY is required "
                    "for uploaded voice-overs, because the subtitles need word timings."
                )
            total_audio = await video.probe_duration(source_audio)

            _progress(job_id, "script", 14, "Storyboarding the narration")
            script = await skills.segment_existing_narration(
                topic=request["topic"], transcript=transcript["text"], duration=total_audio,
                language=language, video_format=request.get("video_format", "16:9"),
                heroes=heroes,
            )
            scenes = script["scenes"]
            _split_uploaded_audio(scenes, transcript["words"], total_audio)
            narration_path = workdir / f"narration{source_audio.suffix or '.mp3'}"
            shutil.copyfile(source_audio, narration_path)
        else:
            if not config.tts_provider_ready(tts_provider):
                raise PipelineError(f"The '{tts_provider}' voice provider has no API key configured.")

            written = (request.get("script") or "").strip()
            if written:
                # The user supplied the words. The Director only decides where
                # the cuts fall and what each shot shows.
                _progress(job_id, "script", 8, "Storyboarding your script")
                script = await skills.segment_written_script(
                    topic=request.get("topic", ""), script=written, language=language,
                    video_format=request.get("video_format", "16:9"), heroes=heroes,
                )
            else:
                _progress(job_id, "script", 8, "Writing the script")
                script = await skills.direct_script(
                    topic=request["topic"], target_seconds=int(request.get("target_seconds", 180)),
                    language=language, tone=request.get("tone", "cinematic documentary"),
                    video_format=request.get("video_format", "16:9"), heroes=heroes,
                    action=request.get("action") or "",
                    cartoon=bool(request.get("animate_actors")),
                )
            scenes = script["scenes"]
            _progress(job_id, "script", 20, f"{len(scenes)} scenes ready")

        _ensure_sids(scenes)
        store.update_job(job_id, result={"title": script.get("title"),
                                         "scene_count": len(scenes)})

        # --- shot list --------------------------------------------------------
        # Decided before the prompts, because a scene covered by three pictures
        # needs three prompts written for it, not one prompt used three times.
        pace = (request.get("shot_pace") or "steady").lower()
        if pace != "steady":
            for scene in scenes:
                count = shots.wanted_count(len(_tokens(scene["narration"])), pace)
                scene["shots"] = [shots.blank(j) for j in range(count)] if count > 1 else []
            _ensure_sids(scenes)
            extra = sum(len(s["shots"]) for s in scenes if s["shots"])
            if extra:
                _progress(job_id, "prompts", 22,
                          f"{extra} kadr — ba'zi sahnalar bir nechta rasmga bo'lindi")

        # --- image prompts ---------------------------------------------------
        _progress(job_id, "prompts", 24, "Designing the look of each scene")
        prompt_pack = await skills.build_image_prompts(
            scenes=scenes, art_style=request.get("art_style", "cinematic photorealistic"),
            video_format=request.get("video_format", "16:9"), heroes=heroes,
            title=script.get("title", request["topic"]),
        )
        scenes = _ensure_sids(prompt_pack["scenes"])

        # --- cartoon staging --------------------------------------------------
        # Before the prompts are used, not after: the picture that gets drawn for
        # a staged scene is a background, and asking for one that already has the
        # characters in it would put every one of them on screen twice.
        if request.get("animate_actors") and heroes and not cast_voices(
                [h["id"] for h in heroes]):
            warnings.append(
                "Hech bir qahramonga ovoz berilmagan — gaplarni diktor o'qiydi. "
                "Kutubxona → Herolar da har biriga ovoz bering.")
        if request.get("animate_actors") and heroes:
            warnings += await stage_cartoon(
                job_id, scenes, heroes, workdir=workdir, provider=image_provider,
                action=request.get("action") or "", language=language,
                video_format=request.get("video_format", "16:9"),
            )
        # Whatever the Imagesmith returned, every shot ends up with a prompt of
        # its own. A model that answers per scene rather than per shot would
        # otherwise draw the same still two or three times and the cuts would
        # be invisible.
        for scene in scenes:
            shots.backfill_prompts(scene)
        _apply_brand(scenes, request, script.get("hook", ""))

        # --- voice ------------------------------------------------------------
        if not uploaded_audio:
            _progress(job_id, "voice", 26, "Recording the voice-over")
            warnings += await _voice_scenes(
                scenes=scenes, targets=scenes, workdir=workdir, provider=tts_provider,
                voice_id=request.get("voice_id"), language=language, job_id=job_id,
                strict=False,
            )
            # Saved before the pictures start, so a failure during the images
            # cannot cost the voice-over that has already been paid for.
            _save_scenes(job_id, scenes)
            _kept_note(job_id, scenes, await keep_media(scenes, job_id))

        # --- images -----------------------------------------------------------
        # Only the first draw of a planned video takes the batch road. A picture
        # redrawn later is one somebody is sitting waiting for.
        _progress(job_id, "images", 48, "Generating scene images")
        warnings += await _render_images(
            scenes=scenes, targets=scenes, workdir=workdir, hero_paths=hero_paths,
            provider=image_provider, aspect=fmt["aspect"], size=(width, height), job_id=job_id,
            batch_wanted=bool(request.get("batch")),
            batch_patience_minutes=float(request.get("batch_patience_minutes") or 0),
        )

        _save_scenes(job_id, scenes, style_bible=prompt_pack.get("style_bible"),
                     warnings=warnings)
        _kept_note(job_id, scenes, await keep_media(scenes, job_id))

        if request.get("auto_render", True):
            await run_render(job_id)
        else:
            _progress(job_id, "review", 72,
                      "Draft ready — review or edit the scenes, then render",
                      status="review")

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


# ── stage 2: render ───────────────────────────────────────────────────────────

async def run_render(job_id: str, *, may_rebuild: bool = False) -> None:
    """Captions, scene clips, cross-fades, audio mix, MP4.

    `may_rebuild` is consent to spend. Pressing Render means "assemble what is
    there", and it used to mean "and quietly re-record anything you cannot find"
    — so a render that failed, on a container that then restarted with an empty
    disk, re-recorded every line and redrew every picture before failing again.
    Round and round, paying each time. Only an explicit «Davom ettirish» buys
    new assets now; Render alone stops and says what is missing.
    """
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])

    try:
        # Marked before anything can fail, so a failure always knows it happened
        # in the render rather than in the draft.
        store.update_job(job_id, step="render")
        scenes = _load_scenes(job)
        if not scenes:
            raise PipelineError("This job has no scenes to render.")

        # A project resumed after a redeploy still knows its scenes; the files
        # are what went missing. Bring them back before deciding what is stale,
        # or every one of them looks like it needs drawing again.
        recovered = await restore_media(scenes)
        if recovered:
            _note(job_id, f"{recovered} ta tayyor fayl saqlangan nusxadan qaytarildi")

        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        width, height = fmt["width"], fmt["height"]
        language = request.get("language", "en")
        uploaded_audio = request.get("narration_audio")

        # Whatever storage could not bring back is remade rather than handed to
        # ffmpeg as a filename. Checked here and not only on resume, because the
        # render is where a missing file actually breaks something.
        lost = forget_missing(scenes)
        if lost and not may_rebuild:
            # Stop rather than spend — and leave the rows exactly as they were.
            # Writing the cleared paths down would turn "this file is missing"
            # into "this asset is stale", and a stale asset is what Render
            # rebuilds without being asked. The refusal would then hold for one
            # attempt and pay on the next, which is the loop this is here to end.
            raise PipelineError(
                f"{lost} ta rasm/ovoz fayli topilmadi — ular saqlanmagan yoki "
                "konteyner bilan o'chgan. «Davom ettirish» bosing: faqat "
                "yo'qolganlari qayta yaratiladi, qolgani qayta to'lanmaydi.")
        if lost:
            _note(job_id, f"{lost} ta fayl topilmadi — o'shalar qayta yaratiladi")
            _save_scenes(job_id, scenes)

        # Any scene edited since the last render still needs its asset built.
        stale_voice = [s for s in scenes if s.get("needs_voice") and not uploaded_audio]
        stale_image = [s for s in scenes if _wants_picture(s)]

        if stale_voice:
            _progress(job_id, "voice", 24, f"Re-recording {len(stale_voice)} edited scene(s)",
                      status="rendering")
            await _voice_scenes(
                scenes=scenes, targets=stale_voice, workdir=workdir,
                provider=(request.get("tts_provider") or config.TTS_PROVIDER).lower(),
                voice_id=request.get("voice_id"), language=language, job_id=job_id,
                base_progress=24, span=14,
            )
        if stale_image:
            heroes = store.get_heroes(request.get("hero_ids") or [])
            hero_paths = _materialize_heroes(workdir, [h["id"] for h in heroes])
            _progress(job_id, "images", 40, f"Regenerating {len(stale_image)} edited image(s)",
                      status="rendering")
            warnings += await _render_images(
                scenes=scenes, targets=stale_image, workdir=workdir, hero_paths=hero_paths,
                provider=(request.get("image_provider") or config.IMAGE_PROVIDER).lower(),
                aspect=fmt["aspect"], size=(width, height), job_id=job_id,
                base_progress=40, span=18, only_stale=True,
            )

        if uploaded_audio:
            narration_path = next(iter(workdir.glob("narration.*")), None)
            if narration_path is None:
                raise PipelineError("The narration audio for this job is missing.")
            total_audio = await video.probe_duration(narration_path)
        else:
            _recompute_starts(scenes)
            narration_path = await video.concat_narration(
                audio_paths=[Path(s["audio_path"]) for s in scenes],
                out_path=workdir / "narration.wav",
            )
            total_audio = sum(float(s["audio_duration"]) for s in scenes)

        # --- captions ---------------------------------------------------------
        _progress(job_id, "captions", 74, "Writing and timing the subtitles", status="rendering")
        captions = await skills.build_captions(
            scenes=scenes, language=language, width=width, height=height
        )
        title_cards = [
            {"text": s["on_screen_text"], "start": s["start"] + 0.25,
             "end": min(s["start"] + 3.0, s["start"] + s["audio_duration"])}
            for s in scenes if s.get("on_screen_text")
        ]

        # Text layers ride in the same ASS file as the captions — libass is
        # already running, so they cost nothing extra and stay just as sharp.
        text_layers: list[dict] = []
        for scene in scenes:
            text_layers += ov.text_layers(
                scene, float(scene.get("start", 0.0)), float(scene["audio_duration"])
            )

        burn_captions = bool(request.get("burn_subtitles", True))
        caption_style = request.get("caption_style") or request.get("subtitle_style", "bold")

        ass_path = workdir / "subtitles.ass"
        subs.write_ass(ass_path, subs.build_ass(
            captions=captions, width=width, height=height,
            # A language whose script the house font cannot draw brings its own.
            font=config.subtitle_font(language),
            style=caption_style, title_cards=title_cards, overlays=text_layers,
            include_captions=burn_captions, language=language,
        ))
        srt_path = workdir / "subtitles.srt"
        srt_path.write_text(subs.build_srt(captions), encoding="utf-8")
        # Turning captions off must not also throw away title cards and text
        # layers, so the file is still burned whenever it has anything in it.
        burn_file = burn_captions or bool(title_cards) or bool(text_layers)

        # --- scene clips -------------------------------------------------------
        _progress(job_id, "clips", 78, "Animating the scenes", status="rendering")
        transition = min(config.TRANSITION_SECONDS,
                         max(0.2, min(s["audio_duration"] for s in scenes) / 2))
        asset_paths = _materialize_assets(workdir, scenes)
        speed = config.speed_profile(request.get("render_speed"))
        clip_durations = [s["audio_duration"] + transition for s in scenes]
        # The cut between two shots of the same scene is quicker than the one
        # between scenes — it is a change of angle, not a change of subject.
        inner = max(0.15, min(0.35, transition / 2))
        clips: list[Path] = [workdir / f"clip_{s['index']:03d}.mp4" for s in scenes]
        made = 0
        lock = asyncio.Lock()

        async def one_clip(scene: dict) -> None:
            nonlocal made
            picture_layers = [
                {**layer, "path": asset_paths[layer["asset_id"]]}
                for layer in ov.image_layers(scene, float(scene["audio_duration"]))
                if layer.get("asset_id") in asset_paths
            ]
            # A scene may be covered by several pictures. The slices are worked
            # out here, against the length this clip will actually run, so the
            # shots always add up to the narration they sit under.
            cuts = shots.plan(scene, scene["audio_duration"] + transition, inner)
            for cut in cuts:
                cut["image"] = Path(cut.get("image_path") or scene["image_path"])
            await video.make_scene_clip(
                shots=cuts,
                duration=scene["audio_duration"] + transition, width=width, height=height,
                inner_transition=inner,
                image_overlays=picture_layers, speed=speed,
                out_path=workdir / f"clip_{scene['index']:03d}.mp4",
            )
            async with lock:
                made += 1
                _progress(job_id, "clips", 78 + int(12 * made / max(len(scenes), 1)),
                          f"Animated scene {made}/{len(scenes)}", status="rendering")

        # Each clip is independent, and animating them is the slowest stage of a
        # render, so they go out together rather than one after another.
        await _gather_limited([one_clip(s) for s in scenes], speed["workers"])

        # --- assemble ----------------------------------------------------------
        _progress(job_id, "render", 90, "Rendering the final video", status="rendering")
        music_path = _materialize_music(workdir, request.get("music_id"))

        title = job.get("result", {}).get("title") or request["topic"]
        out_path = workdir / f"{_slug(title)}.mp4"
        # ffmpeg runs with the project folder as its cwd so every path in the
        # filter graph is a bare filename — no escaping of ':' or '\' needed.
        await video.assemble(
            clips=clips, clip_durations=clip_durations, narration=narration_path,
            total_duration=total_audio, out_path=out_path, workdir=workdir,
            subtitle_file=ass_path if burn_file else None,
            music=music_path,
            music_start=float(request.get("music_start") or 0.0),
            effects=[s.get("transition") or None for s in scenes],
            sfx=_materialize_sfx(workdir, scenes),
            speed=speed,
        )

        # --- publish -----------------------------------------------------------
        _progress(job_id, "publish", 96, "Writing the YouTube metadata", status="rendering")
        publish_pack = await skills.build_publish_pack(
            topic=request["topic"], title=title, scenes=scenes,
            language=language, duration=total_audio,
        )

        video_url, upload_warning = await storage.publish(out_path, f"{job_id}/{out_path.name}")
        await keep_media([{"image_path": str(out_path)}], job_id)
        if upload_warning:
            warnings.append(upload_warning)
        subtitle_url, _ = await storage.publish(srt_path, f"{job_id}/subtitles.srt")
        await keep_media([{"image_path": str(srt_path)}], job_id)

        store.update_job(
            job_id, status="done", step="done", progress=100, error="",
            log=f"Finished — {len(scenes)} scenes, {total_audio:.1f}s",
            result={
                "scenes": scenes,
                "video_url": video_url,
                "download_url": f"/api/jobs/{job_id}/download",
                "subtitle_url": subtitle_url,
                # Kept with the job, not only written to a file. Timing captions
                # costs a model call, and every other subtitle format is these
                # same cues in different punctuation — so they are stored once
                # and converted on demand rather than rebuilt.
                "captions": captions,
                "duration": round(total_audio, 2),
                "warnings": warnings,
                "metadata": publish_pack,
            },
        )

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


async def restyle_music(job_id: str, music_id: str | None, music_start: float) -> None:
    """Put a different track under a finished video — or take the music away.

    Only the soundtrack is rebuilt. The render already saved the narration
    separately, so the bed is mixed against that rather than against the
    finished file's own audio; the picture is copied through untouched, which
    makes this seconds of work instead of a whole render, and means a track can
    be auditioned as many times as it takes without degrading the video.
    """
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    result = job.get("result") or {}
    warnings: list[str] = list(result.get("warnings") or [])

    try:
        workdir = workdir_for(job_id)
        current = _finished_video(workdir)
        if current is None:
            raise PipelineError("This job has no rendered video to add music to.")
        narration_path = next(iter(workdir.glob("narration.*")), None)
        if narration_path is None:
            raise PipelineError(
                "The narration for this video is no longer on disk — render it again first.")

        scenes = _load_scenes(job)
        total = float(result.get("duration") or 0.0) or await video.probe_duration(current)

        _progress(job_id, "music", 40,
                  "Removing the music" if not music_id else "Mixing in the new track",
                  status="rendering")

        music_path = _materialize_music(workdir, music_id)
        if music_id and music_path is None:
            raise PipelineError("That track is no longer in the library.")

        # ffmpeg cannot read and write the same file, so the mix lands beside the
        # original and only replaces it once it has been written in full.
        staging = workdir / "remix.mp4"
        await video.remix_audio(
            video_path=current, narration=narration_path, out_path=staging,
            workdir=workdir, total_duration=total, music=music_path,
            music_start=float(music_start or 0.0),
            sfx=_materialize_sfx(workdir, scenes),
        )
        os.replace(staging, current)

        # Remember the choice: a later re-render should keep the music, not
        # silently go back to whatever the video was created with.
        request["music_id"] = music_id or None
        request["music_start"] = float(music_start or 0.0)
        store.replace_request(job_id, request)

        _progress(job_id, "music", 85, "Publishing the new mix", status="rendering")
        video_url, upload_warning = await storage.publish(current, f"{job_id}/{current.name}")
        await keep_media([{"image_path": str(current)}], job_id)
        if upload_warning:
            warnings.append(upload_warning)

        # The file kept its name, so the browser would happily show the old mix
        # from cache. The counter is what makes the player fetch it again.
        version = int(result.get("audio_version", 0)) + 1
        store.update_job(
            job_id, status="done", step="done", progress=100, error="",
            log="Music removed" if not music_id else "New music mixed in",
            result={
                "video_url": f"{video_url}{'&' if '?' in video_url else '?'}v={version}",
                "audio_version": version,
                "warnings": warnings,
            },
        )

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


async def finished_file(job_id: str) -> Path | None:
    """The rendered MP4, on this disk, whatever it took to get it there.

    Publishing needs the bytes, not a URL — and the disk the render happened on
    may well be gone. Anything that was kept is brought back first, which is the
    difference between "publish it" and "render it again to publish it".
    """
    workdir = workdir_for(job_id)
    local = _finished_video(workdir) if workdir.exists() else None
    if local is not None:
        return local

    job = store.get_job(job_id) or {}
    result = job.get("result") or {}
    # The name is whatever the render wrote; the URL is the only place it survives
    # once the disk has not.
    for url in (result.get("download_url"), result.get("video_url")):
        name = Path(str(url or "").split("?")[0]).name
        if not name.endswith((".mp4", ".mov", ".mkv", ".webm")):
            continue
        target = workdir / name
        if target.exists():
            return target
        key = f"{job_id}/{name}"
        if await storage.fetch(key, target):
            return target
        data = await asyncio.to_thread(store.get_media, key)
        if data is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return target
    return None


async def thumbnail_file(job_id: str) -> Path | None:
    """The first generated cover, if there is one. Never worth failing over."""
    job = store.get_job(job_id) or {}
    shots = (job.get("result") or {}).get("thumbnails") or []
    if not shots:
        return None
    name = Path(str(shots[0]).split("?")[0]).name
    if not name:
        return None
    target = workdir_for(job_id) / "thumbs" / name
    if target.exists():
        return target
    key = f"{job_id}/thumbs/{name}"
    if await storage.fetch(key, target):
        return target
    data = await asyncio.to_thread(store.get_media, key)
    if data is None:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _finished_video(workdir: Path) -> Path | None:
    # `fuse_` files are the half-joined batches a long project is assembled from.
    # They are ordinary MP4s and they are the newest thing in the folder when an
    # assemble dies, so without this a seventy-scene project would hand back
    # twelve silent scenes as its finished video.
    videos = [v for v in workdir.glob("*.mp4")
              if not v.name.startswith(("clip_", "fuse_")) and v.name != "remix.mp4"]
    return max(videos, key=lambda p: p.stat().st_mtime) if videos else None


# ── per-scene regeneration ────────────────────────────────────────────────────

def unfinished(scenes: list[dict], *, uploaded_audio: bool = False) -> dict[str, int]:
    """How much of a draft was never made. Counted in pictures, not scenes.

    *Missing* means there is no picture or no recording at all — the run stopped
    before making it. That is a different thing from *stale*, which is a scene
    whose prompt was edited after it was drawn: stale work exists and is what
    Render redoes. Only missing work is worth offering to carry on, and telling
    the user "six left" about a finished draft they had merely edited would be
    a lie in the direction that costs money.

    A scene split into three shots owes three pictures. Reporting it as one
    would make a job that is 40% done look 80% done at exactly the moment
    somebody is deciding whether to continue it.
    """
    holders = [(s, h) for s in scenes for h in ((s.get("shots") or []) or [s])]
    missing_pictures = sum(1 for _s, h in holders if not h.get("image_path"))
    stale_pictures = sum(
        1 for _s, h in holders if h.get("image_path") and h.get("needs_image"))
    missing_voices = 0 if uploaded_audio else sum(
        1 for s in scenes if not s.get("audio_path"))
    stale_voices = 0 if uploaded_audio else sum(
        1 for s in scenes if s.get("audio_path") and s.get("needs_voice"))
    return {
        "images_left": missing_pictures,
        "voices_left": missing_voices,
        "images_stale": stale_pictures,
        "voices_stale": stale_voices,
        "images_total": len(holders),
        "scenes_total": len(scenes),
        "left": missing_pictures + missing_voices,
    }


# Steps that only happen after the draft was reviewed. A job that broke in one
# of them was on its way to a finished video, and carrying on means finishing —
# not stopping at a review the user already did.
RENDER_STEPS = {"render", "captions", "clips", "assemble", "thumbnails", "publish"}


async def resume_job(job_id: str) -> None:
    """Carry on from wherever a draft stopped, paying only for what is missing.

    A failed run used to be a dead end: the scenes, the voice-over and the
    pictures were all still there, and the only way forward was to start the
    whole thing again. This finishes the gaps instead. A job that never got as
    far as a scene list has nothing to carry on from and starts over.
    """
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    scenes = _load_scenes(job)
    if not scenes:
        await run_draft(job_id)
        return

    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])
    uploaded_audio = request.get("narration_audio")

    try:
        # The files may have been on a container that no longer exists.
        recovered = await restore_media(scenes)
        if recovered:
            _note(job_id, f"{recovered} ta tayyor fayl saqlangan nusxadan qaytarildi")

        forget_missing(scenes)

        left = unfinished(scenes, uploaded_audio=bool(uploaded_audio))
        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        language = request.get("language", "en")
        heroes = store.get_heroes(request.get("hero_ids") or [])
        hero_paths = _materialize_heroes(workdir, [h["id"] for h in heroes])

        if left["voices_left"] and not uploaded_audio:
            _progress(job_id, "voice", 30,
                      f"Davom etmoqda — {left['voices_left']} ta ovoz qoldi")
            warnings += await _voice_scenes(
                scenes=scenes,
                targets=[s for s in scenes if not s.get("audio_path")],
                workdir=workdir,
                provider=(request.get("tts_provider") or config.TTS_PROVIDER).lower(),
                voice_id=request.get("voice_id"), language=language, job_id=job_id,
                strict=False,
            )
            _save_scenes(job_id, scenes)
            _kept_note(job_id, scenes, await keep_media(scenes, job_id))

        if left["images_left"]:
            _progress(job_id, "images", 55,
                      f"Davom etmoqda — {left['images_left']} ta rasm qoldi")
            warnings += await _render_images(
                scenes=scenes, targets=scenes, workdir=workdir, hero_paths=hero_paths,
                provider=(request.get("image_provider") or config.IMAGE_PROVIDER).lower(),
                aspect=fmt["aspect"], size=(fmt["width"], fmt["height"]), job_id=job_id,
                missing_only=True,
            )

        _save_scenes(job_id, scenes, warnings=warnings)
        _kept_note(job_id, scenes, await keep_media(scenes, job_id))

        still = unfinished(scenes, uploaded_audio=bool(uploaded_audio))
        if still["left"]:
            # Some gaps do not close on a retry — a prompt the provider refuses,
            # a line it will not read. Say so plainly and leave the rest usable.
            store.update_job(job_id, error="")
            _progress(job_id, "review", 72,
                      f"{still['left']} ta qism baribir tayyor bo'lmadi — "
                      "qolganini tahrirlab yoki qayta urinib ko'ring",
                      status="review")
            return

        # The failure that sent somebody here is over. Left in place, the card
        # keeps showing the red ffmpeg dump above a project that is now fine.
        store.update_job(job_id, error="")
        # Finish it if that is plainly what was being asked for. Dropping a
        # project back to "press Render" when Render is exactly what it was
        # doing when it broke is one button too many.
        was_rendering = ((job.get("result") or {}).get("failed_at") or
                         job.get("step") or "") in RENDER_STEPS
        if request.get("auto_render", True) or was_rendering:
            _progress(job_id, "render", 74, "Render davom etmoqda", status="rendering")
            await run_render(job_id, may_rebuild=True)
            return
        _progress(job_id, "review", 72, "Hammasi tayyor — Render bosing", status="review")

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


async def relanguage(job_id: str, language: str) -> list[dict]:
    """Rewrite this video's narration in another language, in place.

    Not a clone. «Boshqa tilga» makes a second video because you want both; this
    is for the video you are already working on — you recorded it in the wrong
    language, or decided halfway that it should be Uzbek after all.

    It is all or nothing, for the same reason the narrator is: a video that
    changes language in the middle is a defect, not a feature. The subtitles need
    no separate step, because they are written from the narration at render time
    — change the words and the captions follow.
    """
    job = store.get_job(job_id)
    if job is None:
        return []
    if language not in config.LANGUAGES:
        raise PipelineError(f"Unknown language '{language}'.")
    if job["request"].get("narration_audio"):
        raise PipelineError(
            "Bu videoda o'z audiongiz ishlatilgan — tilini o'zgartirib bo'lmaydi.")

    scenes = _load_scenes(job)
    if not scenes:
        raise PipelineError("Bu videoda sahna yo'q.")

    was = job["request"].get("language", "")
    if was == language:
        return scenes

    _progress(job_id, "translate", 12,
              f"Matn {config.LANGUAGES[language]} tiliga o'girilyapti")
    lines = await skills.translate_lines(
        lines=[s["narration"] for s in scenes],
        target_language=language,
        source_language=was,
        tone=job["request"].get("tone", ""),
        # The pictures are already cut to the old reading, so the new one is
        # asked to take about as long — the same constraint dubbing works under.
        durations=[float(s.get("audio_duration") or 0.0) for s in scenes],
    )

    for scene, line in zip(scenes, lines):
        if line and line.strip():
            scene["narration"] = line.strip()
        # The old timings belong to the old words. Kept, they would put the
        # captions on syllables that are no longer there.
        scene["words"] = []
        scene["needs_voice"] = True

    request = dict(job["request"])
    request["language"] = language
    store.replace_request(job_id, request)
    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Til o'zgardi: {was or '?'} → {language}")
    return scenes


async def regenerate_scene(job_id: str, index: int, *, redo_image: bool, redo_voice: bool,
                           redo_all_voices: bool = False,
                           voice_range: tuple[int, int] | None = None,
                           language: str | None = None) -> None:
    """Rebuild one scene's image and/or voice in place.

    `redo_all_voices` re-records the whole video instead — what you want after
    changing the narrator, rather than waiting for the render to discover it one
    scene at a time.

    `voice_range` is the middle case, and the common one: half a video recorded
    in the wrong voice. Re-recording all of it to fix the second half means
    paying a second time for the half that was already right.

    `language` rewrites the narration first. It always covers the whole video and
    ignores any range, because half a video in another language is not something
    anybody wants.
    """
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])

    try:
        if language and language != request.get("language"):
            await relanguage(job_id, language)
            job = store.get_job(job_id) or job
            request = job["request"]
            # Every line is new, so every line has to be read again.
            redo_voice, redo_all_voices, voice_range = True, True, None

        scenes = _load_scenes(job)
        target = next((s for s in scenes if s["index"] == index), None)
        if target is None:
            raise PipelineError(f"Scene {index} does not exist.")

        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        uploaded_audio = request.get("narration_audio")

        if redo_voice and not uploaded_audio:
            if voice_range is not None:
                low, high = voice_range
                targets = [s for s in scenes if low <= s["index"] <= high]
                said = f"Re-recording scenes {low + 1}–{high + 1} ({len(targets)})"
            elif redo_all_voices:
                targets = scenes
                said = f"Re-recording {len(targets)} scene(s)"
            else:
                targets = [target]
                said = f"Re-recording scene {index + 1}"
            _progress(job_id, "voice", 30, said)
            warnings += await _voice_scenes(
                scenes=scenes, targets=targets, workdir=workdir,
                provider=(request.get("tts_provider") or config.TTS_PROVIDER).lower(),
                voice_id=request.get("voice_id"),
                language=request.get("language", "en"), job_id=job_id,
                base_progress=30, span=20,
                # A long re-record is long enough that losing all of it to one bad
                # line would be cruel; a single scene should say it failed.
                strict=len(targets) == 1,
            )

        if redo_image:
            heroes = store.get_heroes(request.get("hero_ids") or [])
            hero_paths = _materialize_heroes(workdir, [h["id"] for h in heroes])
            _progress(job_id, "images", 60, f"Regenerating the image for scene {index + 1}")
            warnings += await _render_images(
                scenes=scenes, targets=[target], workdir=workdir, hero_paths=hero_paths,
                provider=(request.get("image_provider") or config.IMAGE_PROVIDER).lower(),
                aspect=fmt["aspect"], size=(fmt["width"], fmt["height"]), job_id=job_id,
                base_progress=60, span=20,
            )

        store.update_job(
            job_id, status="review", step="review", progress=72,
            log=f"Scene {index + 1} regenerated",
            result={"scenes": scenes, "warnings": warnings},
        )

    except Exception as exc:  # noqa: BLE001
        _fail(job_id, exc, warnings)


# ── shuffling the running order ───────────────────────────────────────────────

def reorder_scenes(job_id: str, order: list[int]) -> list[dict] | None:
    """Put the scenes in `order` (a permutation of the current indices)."""
    job = store.get_job(job_id)
    if job is None:
        return None
    scenes = _load_scenes(job)
    if sorted(order) != list(range(len(scenes))):
        raise PipelineError("The new order must list every scene exactly once.")

    reordered = _reindex([scenes[i] for i in order])
    _save_scenes(job_id, reordered)
    store.update_job(job_id, log="Scenes reordered")
    return reordered


def delete_scene(job_id: str, index: int) -> list[dict] | None:
    job = store.get_job(job_id)
    if job is None:
        return None
    scenes = _load_scenes(job)
    if not any(s["index"] == index for s in scenes):
        return None
    if len(scenes) <= 1:
        raise PipelineError("A video needs at least one scene.")

    remaining = _reindex([s for s in scenes if s["index"] != index])
    _save_scenes(job_id, remaining, scene_count=len(remaining))
    store.update_job(job_id, log=f"Scene {index + 1} deleted")
    return remaining


async def add_scene(job_id: str, after: int, narration: str) -> None:
    """Insert a scene after `after` and build its prompt, voice and image."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])

    try:
        scenes = _load_scenes(job)
        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])

        position = max(0, min(int(after) + 1, len(scenes)))
        neighbour = scenes[min(position, len(scenes) - 1)] if scenes else {}
        fresh = {
            "sid": store.new_id("sc")[3:],
            "index": position,
            "narration": narration.strip(),
            # The Imagesmith rewrites `visual` into a prompt. A scene added by
            # hand has no shot description of its own, so its narration is the
            # honest starting point — the same thing the Director would have
            # written about.
            "visual": narration.strip(),
            "image_prompt": "",
            "negative_prompt": neighbour.get("negative_prompt", ""),
            "hero_ids": list(neighbour.get("hero_ids") or []),
            "motion": "zoom_in",
            "motion_strength": 1.0,
            "on_screen_text": "",
            "overlays": [],
            "audio_duration": 0.0,
            "needs_image": True,
            "needs_voice": True,
        }
        scenes = _reindex([*scenes[:position], fresh, *scenes[position:]])
        _save_scenes(job_id, scenes, scene_count=len(scenes))

        # The image prompt has to answer to the same style bible as its
        # neighbours, or the new shot will not look like it belongs.
        _progress(job_id, "prompts", 25, "Designing the new scene")
        pack = await skills.build_image_prompts(
            scenes=[fresh],
            art_style=job.get("result", {}).get("style_bible")
            or request.get("art_style", "cinematic photorealistic"),
            video_format=request.get("video_format", "16:9"),
            heroes=store.get_heroes(fresh["hero_ids"]),
            title=job.get("result", {}).get("title") or request.get("topic", ""),
        )
        built = pack["scenes"][0]
        fresh["image_prompt"] = built.get("image_prompt") or narration.strip()
        fresh["negative_prompt"] = built.get("negative_prompt", "")

        if not request.get("narration_audio"):
            _progress(job_id, "voice", 45, "Recording the new scene")
            await _voice_scenes(
                scenes=scenes, targets=[fresh], workdir=workdir,
                provider=(request.get("tts_provider") or config.TTS_PROVIDER).lower(),
                voice_id=request.get("voice_id"),
                language=request.get("language", "en"), job_id=job_id,
                base_progress=45, span=15,
            )

        _progress(job_id, "images", 65, "Generating the new scene image")
        heroes = store.get_heroes(fresh["hero_ids"])
        warnings += await _render_images(
            scenes=scenes, targets=[fresh], workdir=workdir,
            hero_paths=_materialize_heroes(workdir, [h["id"] for h in heroes]),
            provider=(request.get("image_provider") or config.IMAGE_PROVIDER).lower(),
            aspect=fmt["aspect"], size=(fmt["width"], fmt["height"]), job_id=job_id,
            base_progress=65, span=15,
        )

        _reindex(scenes)
        store.update_job(
            job_id, status="review", step="review", progress=72,
            log=f"Scene added at position {position + 1}",
            result={"scenes": scenes, "scene_count": len(scenes), "warnings": warnings},
        )

    except Exception as exc:  # noqa: BLE001
        _fail(job_id, exc, warnings)


# ── thumbnails ────────────────────────────────────────────────────────────────

THUMBNAIL_ANGLES = (
    "extreme close-up on the single most striking detail, dramatic rim light, "
    "huge negative space on the right for a headline",
    "wide establishing shot, one clear subject dead centre, high contrast, "
    "saturated colour grade",
    "the emotional peak of the story, tight on faces, shallow depth of field, "
    "strong single light source",
)


async def make_thumbnails(job_id: str) -> None:
    """Three cover options for the same video, from three different angles."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    result = job.get("result", {})
    warnings: list[str] = list(result.get("warnings") or [])
    previous_status = job["status"]

    try:
        base = (result.get("metadata") or {}).get("thumbnail_prompt") \
            or result.get("title") or request.get("topic", "")
        if not base:
            raise PipelineError("There is nothing to make a thumbnail from yet.")

        provider = (request.get("image_provider") or config.IMAGE_PROVIDER).lower()
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        style = result.get("style_bible") or request.get("art_style", "")
        workdir = workdir_for(job_id)
        heroes = store.get_heroes(request.get("hero_ids") or [])
        hero_paths = _materialize_heroes(workdir, [h["id"] for h in heroes])
        refs = list(hero_paths.values())

        _progress(job_id, "thumbnails", 30, "Designing three cover options",
                  status="running")

        async def one(i: int, angle: str) -> str | None:
            path, warning = await images.generate_image(
                prompt=f"{base}. {angle}. {style}".strip(),
                # A thumbnail with the title baked in fights the one the user
                # will add themselves, and generators spell it wrong anyway.
                negative_prompt="text, letters, words, watermark, logo, caption",
                reference_paths=refs,
                aspect=fmt["aspect"],
                size=(fmt["width"], fmt["height"]),
                provider=provider,
                out_path=workdir / "thumbs" / f"thumb_{i}.png",
                on_retry=_retry_note(job_id, f"Thumbnail {i + 1}"),
            )
            if warning:
                warnings.append(f"Thumbnail {i + 1}: {warning}")
            return _file_url(str(path), int(result.get("thumbnail_version", 0)) + 1)

        urls = await _gather_limited(
            [one(i, angle) for i, angle in enumerate(THUMBNAIL_ANGLES)],
            config.IMAGE_CONCURRENCY,
        )

        store.update_job(
            job_id,
            status=previous_status if previous_status in {"done", "review"} else "done",
            step=previous_status, progress=100 if previous_status == "done" else 72,
            log="Thumbnails ready",
            result={
                "thumbnails": [u for u in urls if u],
                "thumbnail_version": int(result.get("thumbnail_version", 0)) + 1,
                "warnings": warnings,
            },
        )

    except Exception as exc:  # noqa: BLE001
        _fail(job_id, exc, warnings)


# ── one script, several shapes ────────────────────────────────────────────────

def repurpose(job_id: str, video_format: str, regenerate_images: bool) -> str | None:
    """Clone a finished video into another aspect ratio, reusing its assets.

    Voice, timings, captions and layers are shape-independent, so they carry
    over untouched. The stills do not: reused, they are centre-cropped into the
    new frame, which is right for a 16:9 that has room to lose at the sides and
    wrong for one whose subject sits near an edge. Hence the choice.
    """
    job = store.get_job(job_id)
    if job is None:
        return None
    if video_format not in config.FORMATS:
        raise PipelineError(f"Unknown video format '{video_format}'.")

    scenes = _load_scenes(job)
    if not scenes:
        raise PipelineError("This job has no scenes to reuse.")

    request = {
        **job["request"],
        "video_format": video_format,
        "auto_render": False,
        # The script is already written and voiced; a clone must never re-run it.
        "script": None,
        "auto_hook": False,
    }
    clone_id = store.create_job(request)
    source, target = workdir_for(job_id), workdir_for(clone_id)

    for folder in ("audio", "images", "overlays", "heroes", "sfx"):
        if (source / folder).is_dir():
            shutil.copytree(source / folder, target / folder, dirs_exist_ok=True)
    for narration in source.glob("narration.*"):
        shutil.copyfile(narration, target / narration.name)

    clone: list[dict] = []
    for scene in scenes:
        copy = dict(scene)
        for key in ("audio_path", "image_path"):
            if scene.get(key):
                copy[key] = str(target / Path(scene[key]).relative_to(source))
        copy["overlays"] = [dict(o) for o in (scene.get("overlays") or [])]
        copy["needs_image"] = bool(regenerate_images)
        copy["needs_voice"] = False
        clone.append(copy)

    store.update_job(
        clone_id, status="review", step="review", progress=72,
        log=f"Cloned from {job_id} as {video_format}"
            + (" — images will be regenerated" if regenerate_images else ""),
        result={
            "scenes": _reindex(clone),
            "title": job.get("result", {}).get("title"),
            "scene_count": len(clone),
            "style_bible": job.get("result", {}).get("style_bible"),
            "warnings": [],
        },
    )
    return clone_id


# ── Shorts out of a long video ────────────────────────────────────────────────

# What a Short may run to. YouTube takes three minutes now, but the format is
# still a sixty-second one in practice: past that the retention curve is a
# different problem and the thing you have made is a short video, not a Short.
SHORT_MAX_SECONDS = 60.0


async def shorts_for(job_id: str, *, count: int = 3,
                     max_seconds: float = SHORT_MAX_SECONDS) -> list[dict]:
    """Which stretches of this video would stand alone, and how long each is.

    The lengths come from the recorded voice-over rather than from the model, so
    a suggestion says what it will actually cost you before you cut it.
    """
    job = store.get_job(job_id)
    if job is None:
        return []
    scenes = _load_scenes(job)
    if not scenes:
        raise PipelineError("Bu videoda sahna yo'q.")

    request = job.get("request") or {}
    result = job.get("result") or {}
    return await skills.suggest_shorts(
        scenes=scenes,
        language=request.get("language", "en"),
        title=result.get("title") or request.get("topic", ""),
        count=count,
        max_seconds=max_seconds,
    )


async def cut_short(job_id: str, first: int, last: int, *, title: str = "",
                    video_format: str = "9:16",
                    regenerate_images: bool = False) -> str | None:
    """Clone one run of scenes into a Short of its own.

    A Short is cut as a *project*, not out of the finished MP4. Everything the
    long video is made of is still here — the stills, the voice, the layers — so
    the vertical frame is composed rather than cropped out of a wide one, and the
    captions are re-broken for the narrower canvas instead of being shrunk.
    That also means the Short arrives editable: it is a project like any other,
    which is what lets you fix its hook before it goes out.
    """
    job = store.get_job(job_id)
    if job is None:
        return None
    if video_format not in config.FORMATS:
        raise PipelineError(f"Unknown video format '{video_format}'.")

    scenes = _load_scenes(job)
    if not scenes:
        raise PipelineError("Bu videoda sahna yo'q.")

    known = {int(s["index"]) for s in scenes}
    if first not in known:
        raise PipelineError(f"{first}-sahna yo'q.")
    if last < first:
        first, last = last, first
    chosen = [s for s in scenes if first <= int(s["index"]) <= last]
    if not chosen:
        raise PipelineError("Tanlangan oraliqda sahna yo'q.")

    length = sum(float(s.get("audio_duration") or 0.0) for s in chosen)
    request = {
        **job["request"],
        "kind": "short",
        "video_format": video_format,
        "topic": title or job.get("result", {}).get("title") or job["request"].get("topic", ""),
        "target_seconds": max(5, int(round(length))) or 30,
        "auto_render": True,
        # Already written and voiced: a cut must never re-run the script.
        "script": None,
        "auto_hook": False,
        "parent_id": job_id,
        "cut_from": [first, last],
        # The long video's single recording, if it had one, is the whole reading
        # — it is replaced below by a slice per scene.
        "narration_audio": None,
    }
    short_id = store.create_job(request)
    source, target = workdir_for(job_id), workdir_for(short_id)

    for folder in ("audio", "images", "overlays", "heroes", "sfx"):
        if (source / folder).is_dir():
            shutil.copytree(source / folder, target / folder, dirs_exist_ok=True)

    clone: list[dict] = []
    for scene in chosen:
        copy = dict(scene)
        for key in ("audio_path", "image_path"):
            if scene.get(key):
                copy[key] = str(target / Path(scene[key]).relative_to(source))
        copy["overlays"] = [dict(o) for o in (scene.get("overlays") or [])]
        copy["needs_image"] = bool(regenerate_images)
        copy["needs_voice"] = False
        clone.append(copy)

    # A video voiced from an uploaded recording has no per-scene audio — there is
    # one file for the whole reading. The Short cannot carry that: it would be
    # the entire narration over four scenes of picture. Each chosen scene gets
    # its own slice of the recording instead, cut at the timings the storyboard
    # already worked out.
    uploaded = (job.get("request") or {}).get("narration_audio")
    if uploaded and not any(s.get("audio_path") for s in chosen):
        source_audio = Path(uploaded)
        if not source_audio.exists():
            raise PipelineError("Uzun videoning ovoz fayli topilmadi.")
        for scene, copy in zip(chosen, clone):
            begin = float(scene.get("start") or 0.0)
            span = float(scene.get("audio_duration") or 0.0)
            piece = target / "audio" / f"scene_{scene['index']:04d}.mp3"
            await video.slice_audio(source_audio, piece, begin, begin + span)
            copy["audio_path"] = str(piece)

    store.update_job(
        short_id, status="review", step="review", progress=72,
        log=f"Cut from {job_id}, scenes {first}–{last} — {length:.0f}s",
        result={
            "scenes": _reindex(clone),
            "title": title or job.get("result", {}).get("title"),
            "scene_count": len(clone),
            "style_bible": job.get("result", {}).get("style_bible"),
            "warnings": [],
        },
    )
    return short_id


# ── one video, several languages ──────────────────────────────────────────────

async def translate_job(
    job_id: str, language: str, voice_id: str | None, tts_provider: str | None
) -> str | None:
    """Clone a finished video into another language, keeping the pictures.

    The images, layers, camera moves and captions are all language-independent,
    so only the narration is rewritten and re-recorded. Scene lengths then follow
    the new voice-over, which is why the timings are simply recomputed rather
    than forced — a dubbed *project* has no picture to stay in step with, unlike
    dubbing a finished file.
    """
    job = store.get_job(job_id)
    if job is None:
        return None
    if language not in config.LANGUAGES:
        raise PipelineError(f"Unknown language '{language}'.")

    scenes = _load_scenes(job)
    if not scenes:
        raise PipelineError("This job has no scenes to translate.")

    request = {
        **job["request"],
        "language": language,
        "script": None,
        "auto_hook": False,
        "auto_render": False,
        # An uploaded voice-over cannot be reused for a different language.
        "narration_audio": None,
    }
    if voice_id is not None:
        request["voice_id"] = voice_id or None
    if tts_provider:
        request["tts_provider"] = tts_provider

    clone_id = store.create_job(request)
    source, target = workdir_for(job_id), workdir_for(clone_id)
    for folder in ("images", "overlays", "heroes", "sfx"):
        if (source / folder).is_dir():
            shutil.copytree(source / folder, target / folder, dirs_exist_ok=True)

    store.update_job(clone_id, status="running", step="translate", progress=12,
                     log=f"Translating {len(scenes)} scenes into {config.LANGUAGES[language]}")

    try:
        translated = await skills.translate_lines(
            lines=[s["narration"] for s in scenes],
            target_language=language,
            source_language=job["request"].get("language", ""),
            tone=job["request"].get("tone", ""),
            durations=[float(s.get("audio_duration") or 0.0) for s in scenes],
        )
    except Exception as exc:  # noqa: BLE001
        _fail(clone_id, exc, [])
        return clone_id

    clone: list[dict] = []
    for scene, line in zip(scenes, translated):
        copy = dict(scene)
        copy["narration"] = line
        copy["words"] = []
        copy["audio_path"] = None
        copy["needs_voice"] = True
        copy["needs_image"] = False
        copy["overlays"] = [dict(o) for o in (scene.get("overlays") or [])]
        if scene.get("image_path"):
            copy["image_path"] = str(target / Path(scene["image_path"]).relative_to(source))
        clone.append(copy)

    store.update_job(
        clone_id, status="review", step="review", progress=70,
        log="Translated — render to hear it",
        result={
            "scenes": _reindex(clone),
            "title": job.get("result", {}).get("title"),
            "scene_count": len(clone),
            "style_bible": job.get("result", {}).get("style_bible"),
            "warnings": [],
        },
    )
    return clone_id


# ── dubbing a finished video ──────────────────────────────────────────────────

# Segments shorter than this are folded into the next one: a two-word fragment
# translates badly on its own and leaves no room to fit the result.
MIN_SEGMENT = 1.2


def _merge_segments(segments: list[dict], total: float) -> list[dict]:
    merged: list[dict] = []
    for segment in sorted(segments, key=lambda s: s["start"]):
        start = max(0.0, float(segment["start"]))
        end = min(total, float(segment["end"]))
        text = str(segment.get("text", "")).strip()
        if not text or end <= start:
            continue
        if merged and (start - merged[-1]["end"] < 0.35) and \
                (end - merged[-1]["start"] < 14.0) and \
                (merged[-1]["end"] - merged[-1]["start"] < MIN_SEGMENT):
            merged[-1]["end"] = end
            merged[-1]["text"] = f"{merged[-1]['text']} {text}".strip()
        else:
            merged.append({"start": start, "end": end, "text": text})
    return merged


async def run_dub(job_id: str) -> None:
    """Replace a finished video's narration with the same thing in another
    language, leaving the picture untouched."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = []

    try:
        workdir = workdir_for(job_id)
        source = Path(request["source_video"])
        language = request.get("language", "en")
        provider = (request.get("tts_provider") or config.TTS_PROVIDER).lower()

        _progress(job_id, "probe", 6, "Reading the video")
        info = await video.probe_video(source)
        total = float(info["duration"])

        _progress(job_id, "extract", 10, "Separating the soundtrack")
        audio = await video.extract_audio(source, workdir / "original.mp3")

        _progress(job_id, "transcribe", 18,
                  "Listening to the original — this is the slow part")
        segments = _merge_segments(
            await align.transcribe_segments(audio, request.get("source_language") or None),
            total,
        )
        if not segments:
            raise PipelineError(
                "Could not make out any speech in that video. A Gemini or OpenAI "
                "key is needed to transcribe it."
            )
        _progress(job_id, "transcribe", 34, f"{len(segments)} lines heard")

        _progress(job_id, "translate", 40,
                  f"Translating into {config.LANGUAGES.get(language, language)}")
        lines = await skills.translate_lines(
            lines=[s["text"] for s in segments],
            target_language=language,
            source_language=request.get("source_language") or "",
            tone=request.get("tone", ""),
            durations=[s["end"] - s["start"] for s in segments],
        )

        # --- speak it, one line at a time, each fitted to its own slot ---------
        voice_dir = workdir / "dub"
        done = 0
        lock = asyncio.Lock()

        async def speak(index: int, text: str) -> Path | None:
            nonlocal done
            try:
                spoken, _words = await tts.synthesize(
                    text=text, out_path=voice_dir / f"line_{index:04d}",
                    provider=provider, voice_id=request.get("voice_id"),
                    on_retry=_retry_note(job_id, f"Line {index + 1}"),
                    on_wait=_wait_note(job_id, f"Line {index + 1}"),
                )
            except Exception as exc:  # noqa: BLE001 - a lost line is not a lost video
                warnings.append(f"Line {index + 1} could not be voiced: {exc}")
                return None
            finally:
                async with lock:
                    done += 1
                    _progress(job_id, "voice", 45 + int(35 * done / max(len(lines), 1)),
                              f"Voice-over {done}/{len(lines)}")
            return spoken

        spoken = await _gather_limited(
            [speak(i, text) for i, text in enumerate(lines)], config.TTS_CONCURRENCY
        )

        # --- lay them back on the original timeline ---------------------------
        _progress(job_id, "mix", 82, "Placing each line where it was said")
        pieces: list[Path] = []
        if segments[0]["start"] > 0.05:
            pieces.append(await video.silence(voice_dir / "lead.wav", segments[0]["start"]))

        for i, segment in enumerate(segments):
            # The slot runs to the *next* line, so the pause after a sentence is
            # kept rather than squeezed out.
            next_start = segments[i + 1]["start"] if i + 1 < len(segments) else total
            slot = max(0.25, next_start - segment["start"])
            source_audio = spoken[i]
            if source_audio is None:
                pieces.append(await video.silence(voice_dir / f"gap_{i:04d}.wav", slot))
                continue
            pieces.append(await video.fit_speech(
                source=source_audio, out_path=voice_dir / f"fit_{i:04d}.wav",
                speech=segment["end"] - segment["start"], slot=slot,
            ))

        dub_track = await video.concat_audio(pieces, workdir / "dub.wav")

        _progress(job_id, "render", 92, "Putting the new voice on the picture")
        title = _slug(request.get("topic") or source.stem)
        out_path = workdir / f"{title}-{language}.mp4"
        # Keeping the original under the dub preserves the music and effects that
        # were baked into it; at zero the video is narration-only.
        await video.mux_dub(
            video_path=source, dub=dub_track, out_path=out_path,
            original_volume=float(request.get("original_volume") or 0.0),
            speed=config.speed_profile(request.get("render_speed")),
        )

        stored, upload_warning = await storage.publish(out_path, f"{job_id}/{out_path.name}")
        await keep_media([{"image_path": str(out_path)}], job_id)
        if upload_warning:
            warnings.append(upload_warning)
        duration = await video.probe_duration(out_path)
        store.update_job(
            job_id, status="done", step="done", progress=100,
            log=f"Dubbed into {config.LANGUAGES.get(language, language)}",
            result={
                "video_url": stored, "download_url": f"/api/jobs/{job_id}/download",
                "duration": duration, "scene_count": len(segments),
                "title": request.get("topic") or source.stem,
                "transcript": [
                    {**segment, "translation": line}
                    for segment, line in zip(segments, lines)
                ],
                "warnings": warnings,
            },
        )

    except Exception as exc:  # noqa: BLE001
        _fail(job_id, exc, warnings)


def _fail(job_id: str, exc: Exception, warnings: list[str]) -> None:
    detail = str(exc) or exc.__class__.__name__
    job = store.get_job(job_id) or {}
    store.update_job(
        job_id, status="failed", step="failed", error=detail, log=f"Failed: {detail}",
        result={
            "traceback": traceback.format_exc()[-4000:],
            "warnings": warnings,
            # Which stage it broke in. `step` is about to become "failed", and
            # without this nothing downstream can tell a draft that never got
            # going from a render that was one encode away from finishing.
            "failed_at": job.get("step") or "",
        },
    )


# Backwards-compatible alias: a full run is a draft that renders itself.
run_job = run_draft
