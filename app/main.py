"""HTTP layer: hero library, job queue, scene editing, progress polling, downloads."""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, pgstore, pipeline, store
from .models import (
    BrandKit,
    CreateJobRequest,
    HeroPatch,
    JobPatch,
    ModelSettings,
    MusicSwap,
    RegenerateRequest,
    RepurposeRequest,
    SceneInsert,
    SceneOrder,
    ScenePatch,
    ShotIn,
    TranslateRequest,
)
from .providers import catalog, images, storage, tts
from .render import kenburns, overlays as ov
from .render import shots
from .render import subtitles as subs
from .render import video

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}
AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
               ".opus": "audio/opus", ".webm": "audio/webm"}
VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v",
               ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}

# Rendering is CPU- and memory-hungry; more than a couple at once will thrash a
# small Railway container, so jobs queue behind this instead of all starting.
_job_slots = asyncio.Semaphore(max(1, config.MAX_CONCURRENT_JOBS))
_running: set[asyncio.Task] = set()
# Keyed by job id so a job that is waiting on a provider that will never answer
# can be abandoned from the UI instead of being waited out.
_tasks_by_job: dict[str, asyncio.Task] = {}


async def _ensure_bucket_quietly() -> None:
    try:
        await storage.ensure_bucket()
    except Exception:  # noqa: BLE001 - remote storage is optional
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    # A render is server-side work: closing the browser never stopped it, and now
    # neither does the container being replaced under it. Anything interrupted
    # mid-render is picked straight back up, from the scenes already on disk.
    resumable = store.recover_jobs()
    # Model and voice choices made in the UI are stored, not exported to the
    # environment, so they have to be loaded back before the first request.
    config.set_model_overrides(store.get_setting(MODELS_KEY) or {})
    config.set_voice_overrides(store.get_setting(VOICES_KEY) or {})
    # Provisioning the remote bucket is a network call. Never put one between
    # the process starting and the server accepting requests: a wrong storage
    # URL would stall startup and the platform reports that as a failed
    # healthcheck, which looks nothing like the actual misconfiguration.
    if storage.backend() != "local":
        task = asyncio.create_task(_ensure_bucket_quietly())
        _running.add(task)
        task.add_done_callback(_running.discard)

    # Launched after the app is ready to serve, not before: a resumed render is
    # ordinary queued work and must never sit between the process starting and
    # the platform's healthcheck being answered.
    for job_id in resumable:
        store.update_job(job_id, status="rendering", step="render", progress=74,
                         log="Resuming the render after a restart")
        _launch(lambda jid=job_id: pipeline.run_render(jid), job_id)
    yield


app = FastAPI(title="AI Video Studio", version="2.0.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── helpers ───────────────────────────────────────────────────────────────────

def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise HTTPException(status_code=400, detail="Invalid path.")
    return candidate


async def _read_upload(upload: UploadFile, allowed: dict[str, str]) -> tuple[bytes, str, str]:
    """Read an upload into memory, returning (bytes, mime, extension)."""
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{ext or upload.filename}'. "
                   f"Allowed: {', '.join(sorted(allowed))}",
        )
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File is larger than 200 MB.")
        chunks.append(chunk)
    if not chunks:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    return b"".join(chunks), allowed[ext], ext


def _launch(coro_factory, job_id: str | None = None) -> None:
    async def runner() -> None:
        async with _job_slots:
            await coro_factory()

    task = asyncio.create_task(runner())
    _running.add(task)
    task.add_done_callback(_running.discard)
    if job_id:
        # A second run for the same job replaces the first in the map; the older
        # task is already finished by then, because a job only accepts new work
        # from a settled state.
        _tasks_by_job[job_id] = task
        task.add_done_callback(
            lambda t, jid=job_id: _tasks_by_job.pop(jid, None) if _tasks_by_job.get(jid) is t else None
        )


def _job_payload(job: dict[str, Any], *, with_scenes: bool = True) -> dict[str, Any]:
    result = job.get("result") or {}
    request = job.get("request") or {}
    scenes = result.get("scenes") or []
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job.get("step", ""),
        "progress": job.get("progress", 0),
        "kind": request.get("kind", "video"),
        "topic": request.get("topic", ""),
        "video_format": request.get("video_format", "16:9"),
        "language": request.get("language", "en"),
        "auto_render": request.get("auto_render", True),
        "uses_uploaded_audio": bool(request.get("narration_audio")),
        "caption_style": subs.resolve_style(
            request.get("caption_style") or request.get("subtitle_style", "bold")),
        "burn_subtitles": bool(request.get("burn_subtitles", True)),
        "music_id": request.get("music_id") or "",
        "music_start": float(request.get("music_start") or 0.0),
        "error": job.get("error") or None,
        "video_url": result.get("video_url"),
        "download_url": result.get("download_url"),
        "subtitle_url": result.get("subtitle_url"),
        "duration": result.get("duration"),
        "title": result.get("title"),
        "scene_count": result.get("scene_count") or len(scenes),
        "metadata": result.get("metadata"),
        "thumbnails": result.get("thumbnails") or [],
        "transcript": result.get("transcript") or [],
        "scenes": [pipeline.public_scene(job["id"], s) for s in scenes] if with_scenes else None,
        "warnings": result.get("warnings") or [],
        "logs": job.get("logs", [])[-40:],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def _apply_shots(scene: dict[str, Any], incoming: list[dict[str, Any]]) -> None:
    """Replace a scene's shot list, keeping the pictures already drawn.

    Shots are matched by `sid`, which the editor sends back untouched. That is
    what lets someone reorder three shots, or change the fourth's camera move,
    without the first three being redrawn — only a shot whose prompt actually
    changed, or one that is new, is marked for the image stage.
    """
    previous = {s.get("sid"): s for s in (scene.get("shots") or []) if s.get("sid")}
    # Everything this scene already has a picture for, keyed by the prompt that
    # drew it. Used when a shot arrives without an id — which is what happens
    # the first time a scene is split, since its one picture never had a shot
    # id to send back. Matching on the prompt is the honest rule either way: the
    # same words drew it, so redrawing would land in the same place.
    by_prompt: dict[str, dict[str, Any]] = {}
    for holder in [scene, *(scene.get("shots") or [])]:
        key = (holder.get("prompt") or holder.get("image_prompt") or "").strip()
        if key and holder.get("image_path"):
            by_prompt.setdefault(key, holder)

    if len(incoming) < 2:
        # One shot is not a split scene: collapse back to the plain form rather
        # than leaving a single-item list behind for everything else to special-case.
        if incoming:
            only = shots.normalize(incoming[0], 0) or {}
            prompt = only.get("prompt", "").strip()
            if prompt and prompt != scene.get("image_prompt"):
                scene["image_prompt"] = prompt
                scene["needs_image"] = True
            if only.get("motion") in kenburns.MOTIONS:
                scene["motion"] = only["motion"]
            scene["motion_strength"] = only.get("motion_strength", 1.0)
        scene["shots"] = []
        return

    cleaned: list[dict[str, Any]] = []
    for i, raw in enumerate(incoming[:shots.MAX_PER_SCENE]):
        shot = shots.normalize(raw, i)
        if shot is None:
            continue
        if shot.get("motion") not in kenburns.MOTIONS:
            shot["motion"] = shots.MOTION_CYCLE[i % len(shots.MOTION_CYCLE)]
        if shot["transition"] and shot["transition"] not in video.TRANSITION_CHOICES:
            shot["transition"] = ""

        old = previous.get(shot.get("sid"))
        if old is None:
            # No id to match on, so fall back to the prompt. Claimed as it is
            # used, so two shots asking for the same picture do not both take
            # the one file and then fight over it at render time.
            match = by_prompt.pop(shot["prompt"], None)
            if match is not None:
                old = {
                    "image_path": match.get("image_path"),
                    "image_version": match.get("image_version", 0),
                    "negative_prompt": match.get("negative_prompt")
                                       or scene.get("negative_prompt", ""),
                    "prompt": shot["prompt"],
                    "needs_image": bool(match.get("needs_image")),
                }
        if old is None:
            # A brand new shot starts from the scene's own look, so it is a
            # variation on what is already there rather than an empty frame.
            shot.setdefault("prompt", "")
            if not shot["prompt"]:
                framing = shots.FRAMINGS[i % len(shots.FRAMINGS)]
                shot["prompt"] = f"{scene.get('image_prompt', '')} {framing}".strip()
            shot["negative_prompt"] = scene.get("negative_prompt", "")
            shot["needs_image"] = True
        else:
            shot["image_path"] = old.get("image_path")
            shot["image_version"] = old.get("image_version", 0)
            shot["negative_prompt"] = old.get("negative_prompt") or scene.get("negative_prompt", "")
            if not shot["prompt"]:
                shot["prompt"] = old.get("prompt", "")
            shot["needs_image"] = bool(
                old.get("needs_image") or not old.get("image_path")
                or shot["prompt"] != old.get("prompt", ""))
        cleaned.append(shot)

    scene["shots"] = cleaned
    scene["needs_image"] = any(s["needs_image"] for s in cleaned)


def _hero_store() -> dict[str, Any]:
    if not pgstore.enabled():
        return {"backend": "sqlite", "ok": True,
                "note": "Bu konteynerda saqlanadi — deploy qilinganda o'chadi."}
    ok, detail = pgstore.health()
    return {"backend": "postgres", "ok": ok,
            "note": "Postgres — deploydan keyin ham saqlanadi." if ok
                    else f"Postgres ulanmadi: {detail}"}


def _get_job_or_404(job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# ── pages ─────────────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="UI not found.")
    return FileResponse(page)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="/static/favicon.svg")


# ── health ────────────────────────────────────────────────────────────────────

@app.get("/health", include_in_schema=False)
async def health_alias() -> dict[str, Any]:
    """Alias for platforms whose healthcheck defaults to /health."""
    return await health()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ffmpeg": video.ffmpeg_available(),
        "llm": config.llm_ready(),
        "llm_provider": config.llm_provider(),
        "storage": storage.backend(),
        # Heroes are the one thing a container restart must not lose, so where
        # they live — and whether that place is answering — is worth reporting.
        "hero_store": _hero_store(),
        # The pacing that applies to the provider actually configured — a number
        # that belongs to some other provider's key would only mislead.
        "tts_rate_limit": config.tts_rate_limit(config.TTS_PROVIDER),
        "image_providers": {n: config.image_provider_ready(n)
                            for n in ("gemini", "fal", "openai")},
        "tts_providers": {n: config.tts_provider_ready(n)
                          for n in ("elevenlabs", "openai", "gemini")},
        "transcription": bool(config.OPENAI_API_KEY),
        "defaults": {
            "image_provider": config.IMAGE_PROVIDER,
            "tts_provider": config.TTS_PROVIDER,
            "fps": config.FPS,
        },
        # Which model each stage will actually call. Every one of these can be
        # overridden by an environment variable, so the only trustworthy answer
        # to "what is this deployment running?" is the one it reports itself.
        "models": {
            "text": config.model(f"{config.llm_provider()}_text"),
            "image": config.model(f"{config.IMAGE_PROVIDER}_image"),
            "tts": config.model(f"{config.TTS_PROVIDER}_tts"),
        },
        "can_dub": bool(config.GEMINI_API_KEY or config.OPENAI_API_KEY),
        "speeds": [{"id": k, "label": v["label"]} for k, v in config.SPEED_PROFILES.items()],
        # How often the picture changes. The image count is what this really
        # costs, so it is spelled out rather than left to be discovered.
        "shot_paces": [
            {"id": "steady", "label": "Bir sahna — bir rasm", "hint": "eng arzon"},
            {"id": "dynamic", "label": "Jonli", "hint": "uzun sahnalar 2-3 rasmga bo'linadi"},
            {"id": "fast", "label": "Tez", "hint": "har 3 soniyada yangi kadr — ko'p rasm"},
        ],
        "max_shots": shots.MAX_PER_SCENE,
        "cores": config.CPU_COUNT,
        "motions": list(kenburns.MOTIONS),
        "transitions": list(video.TRANSITION_CHOICES),
        "caption_templates": [
            {"id": name, "label": preset.get("label", name),
             "style": subs.resolve_style(name)}
            for name, preset in subs.CAPTION_TEMPLATES.items()
        ],
        "caption_defaults": subs.resolve_style("bold"),
        "overlay_animations": {
            "text": list(ov.TEXT_ANIMATIONS),
            "image": list(ov.IMAGE_ANIMATIONS),
            # Cut-out actors: an image layer that travels across the background.
            "actor": [{"id": k, "label": v["label"]} for k, v in ov.ACTOR_MOVES.items()],
        },
        # The caption budget rides along so the editor's live preview can size
        # its text exactly the way the renderer will, instead of guessing.
        "formats": [
            {"id": k, **v, "caption": config.caption_budget(v["width"], v["height"])}
            for k, v in config.FORMATS.items()
        ],
        "languages": [{"id": k, "label": v} for k, v in config.LANGUAGES.items()],
    }


# ── heroes (stored in the database, blob and all) ─────────────────────────────

def _hero_out(hero: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": hero["id"],
        "name": hero["name"],
        "description": hero.get("description", ""),
        "url": f"/api/heroes/{hero['id']}/image",
    }


@app.get("/api/heroes")
async def list_heroes() -> list[dict[str, Any]]:
    return [_hero_out(h) for h in store.list_heroes()]


@app.post("/api/heroes", status_code=201)
async def create_hero(
    name: str = Form(...),
    description: str = Form(""),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A hero needs a name.")
    data, mime, ext = await _read_upload(image, IMAGE_TYPES)
    hero = store.add_hero(name, description.strip(), data, mime, ext)
    return _hero_out(hero)


@app.patch("/api/heroes/{hero_id}")
async def edit_hero(hero_id: str, patch: HeroPatch) -> dict[str, bool]:
    if not store.update_hero(hero_id, name=patch.name, description=patch.description):
        raise HTTPException(status_code=404, detail="Hero not found, or nothing to change.")
    return {"updated": True}


@app.get("/api/heroes/{hero_id}/image")
async def hero_image(hero_id: str) -> Response:
    blob = store.get_hero_image(hero_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="Hero not found.")
    data, mime, _ext = blob
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.delete("/api/heroes/{hero_id}")
async def remove_hero(hero_id: str) -> dict[str, bool]:
    if not store.delete_hero(hero_id):
        raise HTTPException(status_code=404, detail="Hero not found.")
    return {"deleted": True}


# ── music ─────────────────────────────────────────────────────────────────────

@app.get("/api/music")
async def list_music(kind: str | None = None) -> list[dict[str, Any]]:
    tracks = store.list_music(kind if kind in {"music", "sfx"} else None)
    return [{"id": m["id"], "name": m["name"], "kind": m.get("kind", "music")}
            for m in tracks]


@app.post("/api/music", status_code=201)
async def create_music(
    name: str = Form(...), audio: UploadFile = File(...), kind: str = Form("music")
) -> dict[str, Any]:
    data, mime, ext = await _read_upload(audio, AUDIO_TYPES)
    kind = kind if kind in {"music", "sfx"} else "music"
    track = store.add_music(name.strip() or f"track{ext}", data, mime, ext, kind)
    return {"id": track["id"], "name": track["name"], "kind": track["kind"]}


@app.delete("/api/music/{music_id}")
async def remove_music(music_id: str) -> dict[str, bool]:
    if not store.delete_music(music_id):
        raise HTTPException(status_code=404, detail="Track not found.")
    return {"deleted": True}


# ── overlay assets (stickers, logos, cut-outs) ────────────────────────────────

def _asset_out(asset: dict[str, Any]) -> dict[str, Any]:
    return {"id": asset["id"], "name": asset["name"],
            "url": f"/api/assets/{asset['id']}/image"}


@app.get("/api/assets")
async def list_assets() -> list[dict[str, Any]]:
    return [_asset_out(a) for a in store.list_assets()]


@app.post("/api/assets", status_code=201)
async def create_asset(
    image: UploadFile = File(...), name: str = Form("")
) -> dict[str, Any]:
    data, mime, ext = await _read_upload(image, IMAGE_TYPES)
    label = name.strip() or Path(image.filename or f"layer{ext}").stem
    return _asset_out(store.add_asset(label, data, mime, ext))


@app.post("/api/actors", status_code=201)
async def create_actor(
    image: UploadFile | None = File(None),
    prompt: str = Form(""),
    name: str = Form(""),
    hero_id: str = Form(""),
) -> dict[str, Any]:
    """Make a cut-out: a character on transparency, ready to walk on a scene.

    Three ways in, because the character you want might already exist. An
    uploaded picture is keyed as-is; a hero from the library is redrawn full
    length against the key colour so it can be posed; a prompt draws someone new.
    """
    workdir = config.DATA_DIR / "cutouts"
    workdir.mkdir(parents=True, exist_ok=True)
    stem = store.new_id("cut")
    source = workdir / f"{stem}-src.png"
    label = name.strip()
    # Scratch files, cleaned up whichever way this ends: the asset is the only
    # thing meant to outlive the request, and a refused cut-out is exactly the
    # case where litter would otherwise accumulate.
    litter: list[Path] = [source]

    try:
        return await _build_actor(
            workdir=workdir, stem=stem, source=source, label=label, litter=litter,
            image=image, prompt=prompt, hero_id=hero_id)
    finally:
        for path in litter:
            path.unlink(missing_ok=True)


async def _build_actor(
    *, workdir: Path, stem: str, source: Path, label: str, litter: list[Path],
    image: UploadFile | None, prompt: str, hero_id: str,
) -> dict[str, Any]:
    if image is not None:
        data, _mime, _ext = await _read_upload(image, IMAGE_TYPES)
        source.write_bytes(data)
        label = label or Path(image.filename or "actor").stem
    else:
        wanted = prompt.strip()
        refs: list[Path] = []
        if hero_id:
            blob = store.get_hero_image(hero_id)
            if blob is None:
                raise HTTPException(status_code=404, detail="Bunday hero yo'q.")
            hero = next(iter(store.get_heroes([hero_id])), {})
            ref = workdir / f"{stem}-ref{blob[2] or '.png'}"
            ref.write_bytes(blob[0])
            refs = [ref]
            litter.append(ref)
            label = label or hero.get("name", "Hero")
            wanted = wanted or (
                f"{hero.get('name', 'the character')}, {hero.get('description', '')}".strip(", "))
        if not wanted:
            raise HTTPException(status_code=400,
                                detail="Kim chizilsin? Matn yozing yoki hero tanlang.")
        if not config.image_provider_ready(config.IMAGE_PROVIDER):
            raise HTTPException(status_code=400,
                                detail="Rasm provayderi uchun API kalit yo'q.")

        path, warning = await images.generate_image(
            prompt=f"{wanted}. {images.CUTOUT_INSTRUCTION}",
            negative_prompt=images.CUTOUT_NEGATIVE,
            reference_paths=refs, aspect="1:1", size=(1024, 1024),
            provider=config.IMAGE_PROVIDER, out_path=source, attempts=2,
        )
        if warning:
            raise HTTPException(status_code=502, detail=warning)
        if path != source:
            litter.append(path)
        source = path
        label = label or wanted[:40]

    keyed = workdir / f"{stem}.png"
    litter.append(keyed)
    try:
        await video.cut_out(source, keyed)
    except video.RenderError as exc:
        raise HTTPException(status_code=500, detail=f"Fon ajratilmadi: {exc}") from exc
    if not await video.has_alpha(keyed):
        # Said in terms of what to do next: the two ways this fails are a
        # background the key could not find and a key that took everything.
        raise HTTPException(
            status_code=422,
            detail="Fon ajralmadi — rasmning orqa foni tekis magenta (#FF00FF) "
                   "bo'lishi kerak, qahramonning o'zida esa magenta bo'lmasin.")

    return _asset_out(store.add_asset(label or "Aktyor", keyed.read_bytes(),
                                      "image/png", ".png"))


@app.get("/api/assets/{asset_id}/image")
async def asset_image(asset_id: str) -> Response:
    blob = store.get_asset(asset_id)
    if blob is None:
        raise HTTPException(status_code=404, detail="Layer image not found.")
    data, mime, _ext = blob
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=86400"})


@app.delete("/api/assets/{asset_id}")
async def remove_asset(asset_id: str) -> dict[str, bool]:
    if not store.delete_asset(asset_id):
        raise HTTPException(status_code=404, detail="Layer image not found.")
    return {"deleted": True}


# ── models and voices ─────────────────────────────────────────────────────────

MODELS_KEY = "models"
VOICES_KEY = "voices"


def _models_out() -> dict[str, Any]:
    return {
        "defaults": config.model_defaults(),
        "overrides": dict(config.MODEL_OVERRIDES),
        "active": {key: config.model(key) for key in config.model_defaults()},
        "stages": config.MODEL_STAGES,
        "voices": {p: config.default_voice(p) for p in ("gemini", "openai", "elevenlabs")},
        "in_use": {
            "text": f"{config.llm_provider()}_text",
            "image": f"{config.IMAGE_PROVIDER}_image",
            "tts": f"{config.TTS_PROVIDER}_tts",
        },
    }


@app.get("/api/models")
async def get_models() -> dict[str, Any]:
    return _models_out()


@app.put("/api/models")
async def put_models(body: ModelSettings) -> dict[str, Any]:
    overrides = config.set_model_overrides(body.models)
    voices = config.set_voice_overrides(body.voices)
    store.set_setting(MODELS_KEY, overrides)
    store.set_setting(VOICES_KEY, voices)
    return _models_out()


@app.get("/api/models/available")
async def available_models(provider: str) -> dict[str, Any]:
    """What this key may actually call — asked of the provider, not guessed."""
    return await catalog.list_models(provider)


@app.get("/api/voices")
async def list_voices(provider: str | None = None) -> dict[str, Any]:
    return await catalog.list_voices(provider or config.TTS_PROVIDER)


@app.get("/api/voices/preview")
async def preview_voice(
    provider: str, voice_id: str, language: str = "en"
) -> FileResponse:
    """A short spoken sample, cached so hearing it twice costs nothing."""
    try:
        path = await catalog.preview(provider, voice_id, language)
    except Exception as exc:  # noqa: BLE001 - surfaced to the user as-is
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    media = "audio/mpeg" if path.suffix == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})


# ── brand kit ─────────────────────────────────────────────────────────────────

@app.get("/api/brand")
async def get_brand() -> dict[str, Any]:
    return pipeline.brand()


@app.put("/api/brand")
async def put_brand(kit: BrandKit) -> dict[str, Any]:
    payload = kit.model_dump()
    if payload.get("caption_style"):
        payload["caption_style"] = subs.resolve_style(payload["caption_style"])
    if payload.get("logo_asset_id") and store.get_asset(payload["logo_asset_id"]) is None:
        raise HTTPException(status_code=400, detail="That logo image no longer exists.")
    store.set_setting(pipeline.BRAND_KEY, payload)
    return pipeline.brand()


# ── jobs ──────────────────────────────────────────────────────────────────────

def _validate(request: dict[str, Any]) -> None:
    if not config.llm_ready():
        provider = config.llm_provider()
        key = "ANTHROPIC_API_KEY" if provider == "anthropic" else "GEMINI_API_KEY"
        raise HTTPException(status_code=400,
                            detail=f"{key} is not set — the AI skills cannot run.")
    if not video.ffmpeg_available():
        raise HTTPException(status_code=500, detail="ffmpeg is not installed in this container.")

    image_provider = request.get("image_provider") or config.IMAGE_PROVIDER
    if not config.image_provider_ready(image_provider):
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for the '{image_provider}' image provider.",
        )

    if not request.get("narration_audio"):
        tts_provider = request.get("tts_provider") or config.TTS_PROVIDER
        if not config.tts_provider_ready(tts_provider):
            available = tts.available_providers()
            hint = f" Available: {', '.join(available)}." if available else ""
            raise HTTPException(
                status_code=400,
                detail=f"No API key configured for the '{tts_provider}' voice provider.{hint}",
            )


@app.post("/api/jobs", status_code=202)
async def create_job(request: CreateJobRequest) -> dict[str, Any]:
    payload = request.model_dump()
    # Store only what the caller actually set, so the template's own look shows
    # through instead of being overwritten by a wall of nulls.
    payload["caption_style"] = subs.resolve_style({
        "template": request.subtitle_style,
        **(request.caption_style.patch() if request.caption_style else {}),
    })
    _validate(payload)
    job_id = store.create_job(payload)
    _launch(lambda: pipeline.run_draft(job_id), job_id)
    return {"id": job_id, "status": "queued"}


@app.post("/api/jobs/with-audio", status_code=202)
async def create_job_with_audio(
    topic: str = Form(...),
    video_format: str = Form("16:9"),
    language: str = Form("en"),
    art_style: str = Form("cinematic photorealistic, dramatic lighting, 35mm film"),
    tone: str = Form("cinematic documentary"),
    hero_ids: str = Form(""),
    image_provider: str | None = Form(None),
    music_id: str | None = Form(None),
    subtitle_style: str = Form("bold"),
    burn_subtitles: bool = Form(True),
    auto_render: bool = Form(True),
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """Storyboard a voice-over the user already has, instead of generating one."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Uploaded voice-overs need an OPENAI_API_KEY — the subtitles are timed "
                   "from a transcription of your audio.",
        )

    data, _mime, ext = await _read_upload(audio, AUDIO_TYPES)
    staged = config.DATA_DIR / "uploads" / f"{store.new_id('narr')}{ext}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(data)

    payload = {
        "topic": topic.strip(), "video_format": video_format, "language": language,
        "art_style": art_style, "tone": tone,
        "hero_ids": [h for h in hero_ids.split(",") if h.strip()],
        "image_provider": image_provider, "music_id": music_id or None,
        "subtitle_style": subtitle_style, "burn_subtitles": burn_subtitles,
        "auto_render": auto_render, "narration_audio": str(staged), "target_seconds": 0,
        "caption_style": subs.resolve_style(subtitle_style),
    }
    _validate(payload)
    job_id = store.create_job(payload)
    _launch(lambda: pipeline.run_draft(job_id), job_id)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
async def list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    return [_job_payload(j, with_scenes=False) for j in store.list_jobs(min(limit, 100))]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    return _job_payload(_get_job_or_404(job_id))


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, bool]:
    if not store.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    shutil.rmtree(config.PROJECTS_DIR / job_id, ignore_errors=True)
    return {"deleted": True}


# ── scene editing ─────────────────────────────────────────────────────────────

_BUSY = {"running", "queued", "rendering"}


def _editable_job(job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    job = _get_job_or_404(job_id)
    if job["status"] in _BUSY:
        raise HTTPException(status_code=409,
                            detail="This job is still working — wait for it to finish.")
    scenes = job.get("result", {}).get("scenes") or []
    if not scenes:
        raise HTTPException(status_code=400, detail="This job has no scenes yet.")
    return job, scenes


@app.patch("/api/jobs/{job_id}")
async def edit_job(job_id: str, patch: JobPatch) -> dict[str, Any]:
    """Change what applies to the whole video: caption look, music, burn-in."""
    job = _get_job_or_404(job_id)
    if job["status"] in _BUSY:
        raise HTTPException(status_code=409,
                            detail="This job is still working — wait for it to finish.")

    request = dict(job["request"])
    if patch.caption_style is not None:
        fields = patch.caption_style.patch()
        current = subs.resolve_style(
            request.get("caption_style") or request.get("subtitle_style", "bold"))
        # Naming a *different* template means "give me that look", so the patch
        # starts from the template rather than from knobs left over from the old
        # one. Re-sending the same template is an ordinary tweak and merges.
        base = current if fields.get("template", current["template"]) == current["template"] \
            else subs.resolve_style(fields["template"])
        resolved = subs.resolve_style({**base, **fields})
        request["caption_style"] = resolved
        request["subtitle_style"] = resolved["template"]
    if patch.burn_subtitles is not None:
        request["burn_subtitles"] = patch.burn_subtitles
    if patch.music_id is not None:
        request["music_id"] = patch.music_id or None
    if patch.music_start is not None:
        request["music_start"] = float(patch.music_start)

    revoice = _apply_voice(job_id, request, patch.tts_provider, patch.voice_id)

    store.replace_request(job_id, request)
    if revoice:
        # The narrator belongs to the video, so every line is stale at once.
        # They are marked rather than re-recorded here: the render stage already
        # knows how to rebuild what is stale, and doing it now would make a
        # settings change take minutes and cost a voice quota unasked.
        scenes = (store.get_job(job_id).get("result") or {}).get("scenes") or []
        for scene in scenes:
            scene["needs_voice"] = True
        store.update_job(job_id, result={"scenes": scenes},
                         log=f"Voice changed — {len(scenes)} scene(s) will be re-recorded")
    return _job_payload(_get_job_or_404(job_id), with_scenes=False)


def _apply_voice(job_id: str, request: dict[str, Any],
                 provider: str | None, voice_id: str | None) -> bool:
    """Point the job at a different narrator. True when something changed.

    An uploaded voice-over is fixed audio — there is no narrator to swap — so
    the request is refused rather than silently ignored.
    """
    if provider is None and voice_id is None:
        return False
    if request.get("narration_audio"):
        raise HTTPException(
            status_code=400,
            detail="Bu videoda o'z audiongiz ishlatilgan — ovozni almashtirib bo'lmaydi.")

    changed = False
    if provider is not None:
        chosen = (provider or "").strip().lower()
        if chosen and not config.tts_provider_ready(chosen):
            raise HTTPException(
                status_code=400,
                detail=f"'{chosen}' ovoz provayderi uchun API kalit yo'q.")
        if chosen != (request.get("tts_provider") or ""):
            request["tts_provider"] = chosen or None
            changed = True
    if voice_id is not None:
        chosen = (voice_id or "").strip()
        if chosen != (request.get("voice_id") or ""):
            request["voice_id"] = chosen or None
            changed = True
    return changed


@app.patch("/api/jobs/{job_id}/scenes/{index}")
async def edit_scene(job_id: str, index: int, patch: ScenePatch) -> dict[str, Any]:
    """Rewrite one scene. Changed text marks the scene's assets stale."""
    job, scenes = _editable_job(job_id)
    scene = next((s for s in scenes if s["index"] == index), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")

    uploaded_audio = bool(job["request"].get("narration_audio"))

    if patch.narration is not None:
        narration = patch.narration.strip()
        if not narration:
            raise HTTPException(status_code=400, detail="Narration cannot be empty.")
        if narration != scene.get("narration"):
            scene["narration"] = narration
            # An uploaded voice-over is fixed audio: editing the text only fixes
            # the subtitle wording, so don't mark it for re-recording.
            scene["needs_voice"] = not uploaded_audio

    if patch.image_prompt is not None:
        prompt = patch.image_prompt.strip()
        if prompt and prompt != scene.get("image_prompt"):
            scene["image_prompt"] = prompt
            scene["needs_image"] = True

    if patch.motion is not None:
        if patch.motion not in kenburns.MOTIONS:
            raise HTTPException(status_code=400,
                                detail=f"Unknown camera move '{patch.motion}'.")
        scene["motion"] = patch.motion

    if patch.motion_strength is not None:
        scene["motion_strength"] = round(float(patch.motion_strength), 2)

    if patch.overlays is not None:
        known = {a["id"] for a in store.list_assets()}
        # A picture deleted from the library takes its layers with it rather
        # than blocking every later edit to the scene it was used on.
        scene["overlays"] = [
            layer for layer in ov.normalize_all([o.model_dump() for o in patch.overlays])
            if layer["type"] != "image" or layer["asset_id"] in known
        ]

    if patch.transition is not None:
        chosen = patch.transition.strip()
        if chosen and chosen not in video.TRANSITION_CHOICES:
            raise HTTPException(status_code=400, detail=f"Unknown transition '{chosen}'.")
        # Empty means "let the renderer pick", which is the default behaviour.
        scene["transition"] = chosen or None

    if patch.on_screen_text is not None:
        scene["on_screen_text"] = patch.on_screen_text.strip()

    if patch.sfx_id is not None:
        chosen = patch.sfx_id.strip()
        if chosen and store.get_music_audio(chosen) is None:
            raise HTTPException(status_code=400, detail="That sound effect no longer exists.")
        scene["sfx_id"] = chosen or None
    if patch.sfx_volume is not None:
        scene["sfx_volume"] = round(float(patch.sfx_volume), 2)
    if patch.sfx_offset is not None:
        scene["sfx_offset"] = round(float(patch.sfx_offset), 2)

    if patch.hero_ids is not None:
        known = {h["id"] for h in store.get_heroes(patch.hero_ids)}
        new_ids = [h for h in patch.hero_ids if h in known]
        if new_ids != scene.get("hero_ids"):
            scene["hero_ids"] = new_ids
            scene["needs_image"] = True
            for shot in scene.get("shots") or []:
                shot["needs_image"] = True

    if patch.shots is not None:
        _apply_shots(scene, [s.model_dump() for s in patch.shots])

    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Scene {index + 1} edited")
    return pipeline.public_scene(job_id, scene)


@app.post("/api/jobs/{job_id}/scenes", status_code=202)
async def insert_scene(job_id: str, body: SceneInsert) -> dict[str, Any]:
    """Add a scene, then write its prompt, record it and draw it."""
    _editable_job(job_id)
    store.update_job(job_id, status="running", step="add-scene", progress=10)
    _launch(lambda: pipeline.add_scene(job_id, body.after, body.narration), job_id)
    return {"id": job_id, "status": "running"}


@app.delete("/api/jobs/{job_id}/scenes/{index}")
async def remove_scene(job_id: str, index: int) -> list[dict[str, Any]]:
    _editable_job(job_id)
    try:
        scenes = pipeline.delete_scene(job_id, index)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if scenes is None:
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    return [pipeline.public_scene(job_id, s) for s in scenes]


@app.post("/api/jobs/{job_id}/scenes/order")
async def order_scenes(job_id: str, body: SceneOrder) -> list[dict[str, Any]]:
    _editable_job(job_id)
    try:
        scenes = pipeline.reorder_scenes(job_id, body.order)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if scenes is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return [pipeline.public_scene(job_id, s) for s in scenes]


@app.post("/api/jobs/{job_id}/scenes/{index}/regenerate", status_code=202)
async def regenerate_scene(job_id: str, index: int, body: RegenerateRequest) -> dict[str, Any]:
    job, scenes = _editable_job(job_id)
    if not any(s["index"] == index for s in scenes):
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    if not (body.image or body.voice):
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")

    redo_voice = body.voice and not job["request"].get("narration_audio")

    # Re-recording is exactly when you notice the voice was wrong, so it can be
    # changed here. It applies to the whole video — a narrator who changes
    # halfway through is a defect — and the other scenes are marked rather than
    # re-recorded now, so one scene's fix does not quietly become a whole
    # re-record you did not ask for.
    request = dict(job["request"])
    if _apply_voice(job_id, request, body.tts_provider, body.voice_id):
        store.replace_request(job_id, request)
        for scene in scenes:
            if scene["index"] != index:
                scene["needs_voice"] = True
        store.update_job(job_id, result={"scenes": scenes},
                         log="Voice changed — other scenes will follow on render")
        redo_voice = redo_voice or bool(body.voice)

    store.update_job(job_id, status="running", step="regenerate", progress=10)
    _launch(lambda: pipeline.regenerate_scene(
        job_id, index, redo_image=body.image, redo_voice=redo_voice,
        redo_all_voices=body.all_scenes), job_id)
    return {"id": job_id, "status": "running"}


@app.post("/api/jobs/{job_id}/scenes/{index}/image")
async def upload_scene_image(
    job_id: str, index: int, image: UploadFile = File(...)
) -> dict[str, Any]:
    """Use your own still for a scene instead of a generated one."""
    _editable_job(job_id)
    data, _mime, _ext = await _read_upload(image, IMAGE_TYPES)
    scene = pipeline.replace_scene_image(job_id, index, data)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    return pipeline.public_scene(job_id, scene)


@app.post("/api/jobs/{job_id}/render", status_code=202)
async def render_job(job_id: str) -> dict[str, Any]:
    """Finish a reviewed draft, or re-render a finished video after edits."""
    _editable_job(job_id)
    store.update_job(job_id, status="rendering", step="render", progress=74)
    _launch(lambda: pipeline.run_render(job_id), job_id)
    return {"id": job_id, "status": "rendering"}


@app.post("/api/jobs/{job_id}/music", status_code=202)
async def swap_music(job_id: str, body: MusicSwap) -> dict[str, Any]:
    """Add, change or remove the music under a finished video.

    Separate from the render endpoint because it is a different size of job:
    only the audio is rebuilt and the picture is copied through, so a track can
    be tried, judged and swapped again in seconds.
    """
    job = _get_job_or_404(job_id)
    if job["status"] in _BUSY:
        raise HTTPException(status_code=409, detail="This job is still working.")
    if job["status"] != "done":
        raise HTTPException(
            status_code=400,
            detail="Render the video first — music is mixed into a finished file.")
    if body.music_id and store.get_music_audio(body.music_id) is None:
        raise HTTPException(status_code=404, detail="That track is not in the library.")

    store.update_job(job_id, status="rendering", step="music", progress=10)
    _launch(lambda: pipeline.restyle_music(job_id, body.music_id or None, body.music_start),
            job_id)
    return {"id": job_id, "status": "rendering"}


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, Any]:
    """Abandon a job that is waiting on something that is never going to answer.

    The task is cancelled rather than left to time out, and the row is settled
    here rather than in the pipeline: `CancelledError` is a `BaseException`, so
    it flies straight past the handlers that would normally record the outcome.
    """
    job = _get_job_or_404(job_id)
    if job["status"] not in _BUSY:
        raise HTTPException(status_code=409, detail="This job is not running.")

    task = _tasks_by_job.pop(job_id, None)
    if task and not task.done():
        task.cancel()

    scenes = (job.get("result") or {}).get("scenes") or []
    store.update_job(
        job_id,
        status="review" if scenes else "failed",
        step="review" if scenes else "failed",
        progress=72 if scenes else job.get("progress", 0),
        # Scenes are only written between stages, so a cancel during the first
        # pass has nothing to go back to; one that lands later keeps the draft.
        error="" if scenes else "Stopped before it finished.",
        log="Stopped by you.",
    )
    return _job_payload(_get_job_or_404(job_id))


@app.post("/api/jobs/{job_id}/thumbnails", status_code=202)
async def make_thumbnails(job_id: str) -> dict[str, Any]:
    """Three cover options, drawn from the thumbnail prompt the publisher wrote."""
    job = _get_job_or_404(job_id)
    if job["status"] in _BUSY:
        raise HTTPException(status_code=409, detail="This job is still working.")
    provider = (job["request"].get("image_provider") or config.IMAGE_PROVIDER).lower()
    if not config.image_provider_ready(provider):
        raise HTTPException(status_code=400,
                            detail=f"No API key configured for the '{provider}' image provider.")
    store.update_job(job_id, status="running", step="thumbnails", progress=10)
    _launch(lambda: pipeline.make_thumbnails(job_id), job_id)
    return {"id": job_id, "status": "running"}


@app.post("/api/jobs/{job_id}/translate", status_code=202)
async def translate_job(job_id: str, body: TranslateRequest) -> dict[str, Any]:
    """Make this video again in another language, reusing every picture."""
    _editable_job(job_id)
    provider = (body.tts_provider or config.TTS_PROVIDER).lower()
    if not config.tts_provider_ready(provider):
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for the '{provider}' voice provider.")
    if not config.llm_ready():
        raise HTTPException(status_code=400, detail="The translator needs an AI key.")

    try:
        clone_id = await pipeline.translate_job(
            job_id, body.language, body.voice_id, body.tts_provider)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if clone_id is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"id": clone_id, "status": store.get_job(clone_id)["status"]}


@app.post("/api/dub", status_code=202)
async def dub_video(
    video_file: UploadFile = File(..., alias="video"),
    language: str = Form(...),
    source_language: str = Form(""),
    tts_provider: str | None = Form(None),
    voice_id: str | None = Form(None),
    original_volume: float = Form(0.0),
    render_speed: str = Form("balanced"),
    shot_pace: str = Form("steady"),
    topic: str = Form(""),
) -> dict[str, Any]:
    """Dub a finished video: same picture, narration in another language."""
    if language not in config.LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Unknown language '{language}'.")
    if not config.llm_ready():
        raise HTTPException(status_code=400, detail="The translator needs an AI key.")
    if not (config.GEMINI_API_KEY or config.OPENAI_API_KEY):
        raise HTTPException(
            status_code=400,
            detail="Transcribing the original needs a Gemini or OpenAI key.")
    provider = (tts_provider or config.TTS_PROVIDER).lower()
    if not config.tts_provider_ready(provider):
        raise HTTPException(
            status_code=400,
            detail=f"No API key configured for the '{provider}' voice provider.")
    if not video.ffmpeg_available():
        raise HTTPException(status_code=500, detail="ffmpeg is not installed in this container.")

    data, _mime, ext = await _read_upload(video_file, VIDEO_TYPES)
    staged = config.DATA_DIR / "uploads" / f"{store.new_id('src')}{ext}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(data)

    job_id = store.create_job({
        "kind": "dub",
        "topic": topic.strip() or Path(video_file.filename or "video").stem,
        "source_video": str(staged),
        "language": language,
        "source_language": source_language.strip(),
        "tts_provider": tts_provider or None,
        "voice_id": (voice_id or "").strip() or None,
        "original_volume": max(0.0, min(1.0, original_volume)),
        "render_speed": render_speed,
        "shot_pace": shot_pace if shot_pace in {"steady", "dynamic", "fast"} else "steady",
        "video_format": "16:9",
        "auto_render": True,
    })
    _launch(lambda: pipeline.run_dub(job_id), job_id)
    return {"id": job_id, "status": "queued"}


@app.post("/api/jobs/{job_id}/repurpose", status_code=201)
async def repurpose_job(job_id: str, body: RepurposeRequest) -> dict[str, Any]:
    """Clone this video into another aspect ratio, reusing the voice and timings."""
    _editable_job(job_id)
    try:
        clone_id = pipeline.repurpose(job_id, body.video_format, body.regenerate_images)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if clone_id is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {"id": clone_id, "status": "review"}


# ── files ─────────────────────────────────────────────────────────────────────

@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str) -> FileResponse:
    _get_job_or_404(job_id)
    folder = config.PROJECTS_DIR / job_id
    videos = [v for v in folder.glob("*.mp4") if not v.name.startswith("clip_")] \
        if folder.exists() else []
    if not videos:
        raise HTTPException(status_code=404, detail="This job has no rendered video yet.")
    newest = max(videos, key=lambda p: p.stat().st_mtime)
    return FileResponse(newest, media_type="video/mp4", filename=newest.name)


@app.get("/api/files/{path:path}")
async def project_file(path: str) -> FileResponse:
    target = _safe_child(config.PROJECTS_DIR, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})
