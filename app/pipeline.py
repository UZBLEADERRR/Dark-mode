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
import re
import shutil
import traceback
import unicodedata
from pathlib import Path
from typing import Any

from . import config, skills, store
from .providers import align, images, storage, tts
from .render import overlays as ov
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
        "overlays": scene.get("overlays") or [],
        "sfx_id": scene.get("sfx_id") or "",
        "sfx_volume": round(float(scene.get("sfx_volume") or 1.0), 2),
        "sfx_offset": round(float(scene.get("sfx_offset") or 0.0), 2),
        "start": round(float(scene.get("start", 0.0)), 2),
        "duration": round(float(scene.get("audio_duration", 0.0)), 2),
        "image_url": _file_url(scene.get("image_path"), scene.get("image_version", 0)),
        # The editor plays this to preview the scene with its captions and
        # layers running in step, which is why the word timings ride along.
        "audio_url": _file_url(scene.get("audio_path")),
        "words": scene.get("words") or [],
        "needs_image": bool(scene.get("needs_image")),
        "needs_voice": bool(scene.get("needs_voice")),
    }


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
) -> None:
    """Synthesize `targets` (a subset of `scenes`) and refresh every start time."""
    audio_dir = workdir / "audio"
    align_provider = config.resolve_align_provider(provider)
    done = 0
    lock = asyncio.Lock()

    async def one(scene: dict) -> None:
        nonlocal done
        path, provider_words = await tts.synthesize(
            text=scene["narration"],
            out_path=audio_dir / f"scene_{scene['sid']}",
            provider=provider,
            voice_id=voice_id,
        )
        raw_duration = await video.probe_duration(path)
        words = await align.words_for(
            audio_path=path,
            text=scene["narration"],
            duration=raw_duration,
            provider=align_provider,
            language=language,
            provider_words=provider_words,
        )
        scene["audio_path"] = str(path)
        scene["audio_duration"] = raw_duration + video.SCENE_GAP
        scene["words"] = words
        scene["needs_voice"] = False
        async with lock:
            done += 1
            _progress(job_id, "voice", base_progress + int(span * done / max(len(targets), 1)),
                      f"Voice-over {done}/{len(targets)}")

    if targets:
        await _gather_limited([one(scene) for scene in targets], config.TTS_CONCURRENCY)
    _recompute_starts(scenes)


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
) -> list[str]:
    image_dir = workdir / "images"
    warnings: list[str] = []
    done = 0
    lock = asyncio.Lock()

    async def one(scene: dict) -> None:
        nonlocal done
        refs = [hero_paths[h] for h in scene.get("hero_ids", []) if h in hero_paths]
        path, warning = await images.generate_image(
            prompt=scene["image_prompt"],
            negative_prompt=scene.get("negative_prompt", ""),
            reference_paths=refs,
            aspect=aspect,
            size=size,
            provider=provider,
            out_path=image_dir / f"scene_{scene['sid']}.png",
        )
        scene["image_path"] = str(path)
        scene["needs_image"] = False
        scene["image_version"] = int(scene.get("image_version", 0)) + 1
        async with lock:
            done += 1
            if warning:
                warnings.append(f"Scene {scene['index'] + 1}: {warning}")
            _progress(job_id, "images", base_progress + int(span * done / max(len(targets), 1)),
                      f"Scene image {done}/{len(targets)}")

    if targets:
        await _gather_limited([one(scene) for scene in targets], config.IMAGE_CONCURRENCY)
    return warnings


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
                )
            scenes = script["scenes"]
            _progress(job_id, "script", 20, f"{len(scenes)} scenes ready")

        _ensure_sids(scenes)
        store.update_job(job_id, result={"title": script.get("title"),
                                         "scene_count": len(scenes)})

        # --- image prompts ---------------------------------------------------
        _progress(job_id, "prompts", 24, "Designing the look of each scene")
        prompt_pack = await skills.build_image_prompts(
            scenes=scenes, art_style=request.get("art_style", "cinematic photorealistic"),
            video_format=request.get("video_format", "16:9"), heroes=heroes,
            title=script.get("title", request["topic"]),
        )
        scenes = _ensure_sids(prompt_pack["scenes"])
        _apply_brand(scenes, request, script.get("hook", ""))

        # --- voice ------------------------------------------------------------
        if not uploaded_audio:
            _progress(job_id, "voice", 26, "Recording the voice-over")
            await _voice_scenes(
                scenes=scenes, targets=scenes, workdir=workdir, provider=tts_provider,
                voice_id=request.get("voice_id"), language=language, job_id=job_id,
            )

        # --- images -----------------------------------------------------------
        _progress(job_id, "images", 48, "Generating scene images")
        warnings += await _render_images(
            scenes=scenes, targets=scenes, workdir=workdir, hero_paths=hero_paths,
            provider=image_provider, aspect=fmt["aspect"], size=(width, height), job_id=job_id,
        )

        _save_scenes(job_id, scenes, style_bible=prompt_pack.get("style_bible"),
                     warnings=warnings)

        if request.get("auto_render", True):
            await run_render(job_id)
        else:
            _progress(job_id, "review", 72,
                      "Draft ready — review or edit the scenes, then render",
                      status="review")

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


# ── stage 2: render ───────────────────────────────────────────────────────────

async def run_render(job_id: str) -> None:
    """Captions, scene clips, cross-fades, audio mix, MP4."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])

    try:
        scenes = _load_scenes(job)
        if not scenes:
            raise PipelineError("This job has no scenes to render.")

        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        width, height = fmt["width"], fmt["height"]
        language = request.get("language", "en")
        uploaded_audio = request.get("narration_audio")

        # Any scene edited since the last render still needs its asset built.
        stale_voice = [s for s in scenes if s.get("needs_voice") and not uploaded_audio]
        stale_image = [s for s in scenes if s.get("needs_image") or not s.get("image_path")]

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
                base_progress=40, span=18,
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
            captions=captions, width=width, height=height, font=config.SUBTITLE_FONT,
            style=caption_style, title_cards=title_cards, overlays=text_layers,
            include_captions=burn_captions,
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
            await video.make_scene_clip(
                image=Path(scene["image_path"]), motion=scene.get("motion", "zoom_in"),
                duration=scene["audio_duration"] + transition, width=width, height=height,
                strength=float(scene.get("motion_strength") or 1.0),
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
        if upload_warning:
            warnings.append(upload_warning)
        subtitle_url, _ = await storage.publish(srt_path, f"{job_id}/subtitles.srt")

        store.update_job(
            job_id, status="done", step="done", progress=100, error="",
            log=f"Finished — {len(scenes)} scenes, {total_audio:.1f}s",
            result={
                "scenes": scenes,
                "video_url": video_url,
                "download_url": f"/api/jobs/{job_id}/download",
                "subtitle_url": subtitle_url,
                "duration": round(total_audio, 2),
                "warnings": warnings,
                "metadata": publish_pack,
            },
        )

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        _fail(job_id, exc, warnings)


# ── per-scene regeneration ────────────────────────────────────────────────────

async def regenerate_scene(job_id: str, index: int, *, redo_image: bool, redo_voice: bool) -> None:
    """Rebuild one scene's image and/or voice in place."""
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]
    warnings: list[str] = list(job.get("result", {}).get("warnings") or [])

    try:
        scenes = _load_scenes(job)
        target = next((s for s in scenes if s["index"] == index), None)
        if target is None:
            raise PipelineError(f"Scene {index} does not exist.")

        workdir = workdir_for(job_id)
        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        uploaded_audio = request.get("narration_audio")

        if redo_voice and not uploaded_audio:
            _progress(job_id, "voice", 30, f"Re-recording scene {index + 1}")
            await _voice_scenes(
                scenes=scenes, targets=[target], workdir=workdir,
                provider=(request.get("tts_provider") or config.TTS_PROVIDER).lower(),
                voice_id=request.get("voice_id"),
                language=request.get("language", "en"), job_id=job_id,
                base_progress=30, span=20,
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
    store.update_job(
        job_id, status="failed", step="failed", error=detail, log=f"Failed: {detail}",
        result={"traceback": traceback.format_exc()[-4000:], "warnings": warnings},
    )


# Backwards-compatible alias: a full run is a draft that renders itself.
run_job = run_draft
