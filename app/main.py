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

from . import config, pipeline, store
from .models import CreateJobRequest, HeroPatch, RegenerateRequest, ScenePatch
from .providers import storage, tts
from .render import kenburns, video

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}
AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
               ".aac": "audio/aac", ".ogg": "audio/ogg", ".flac": "audio/flac",
               ".opus": "audio/opus", ".webm": "audio/webm"}

# Rendering is CPU- and memory-hungry; more than a couple at once will thrash a
# small Railway container, so jobs queue behind this instead of all starting.
_job_slots = asyncio.Semaphore(max(1, config.MAX_CONCURRENT_JOBS))
_running: set[asyncio.Task] = set()


async def _ensure_bucket_quietly() -> None:
    try:
        await storage.ensure_bucket()
    except Exception:  # noqa: BLE001 - remote storage is optional
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    store.reset_stale_jobs()
    # Provisioning the remote bucket is a network call. Never put one between
    # the process starting and the server accepting requests: a wrong storage
    # URL would stall startup and the platform reports that as a failed
    # healthcheck, which looks nothing like the actual misconfiguration.
    if storage.backend() != "local":
        task = asyncio.create_task(_ensure_bucket_quietly())
        _running.add(task)
        task.add_done_callback(_running.discard)
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


def _launch(coro_factory) -> None:
    async def runner() -> None:
        async with _job_slots:
            await coro_factory()

    task = asyncio.create_task(runner())
    _running.add(task)
    task.add_done_callback(_running.discard)


def _job_payload(job: dict[str, Any], *, with_scenes: bool = True) -> dict[str, Any]:
    result = job.get("result") or {}
    request = job.get("request") or {}
    scenes = result.get("scenes") or []
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job.get("step", ""),
        "progress": job.get("progress", 0),
        "topic": request.get("topic", ""),
        "video_format": request.get("video_format", "16:9"),
        "language": request.get("language", "en"),
        "auto_render": request.get("auto_render", True),
        "uses_uploaded_audio": bool(request.get("narration_audio")),
        "error": job.get("error") or None,
        "video_url": result.get("video_url"),
        "download_url": result.get("download_url"),
        "subtitle_url": result.get("subtitle_url"),
        "duration": result.get("duration"),
        "title": result.get("title"),
        "scene_count": result.get("scene_count") or len(scenes),
        "metadata": result.get("metadata"),
        "scenes": [pipeline.public_scene(job["id"], s) for s in scenes] if with_scenes else None,
        "warnings": result.get("warnings") or [],
        "logs": job.get("logs", [])[-40:],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


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
        "motions": list(kenburns.MOTIONS),
        "formats": [{"id": k, **v} for k, v in config.FORMATS.items()],
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
async def list_music() -> list[dict[str, Any]]:
    return [{"id": m["id"], "name": m["name"]} for m in store.list_music()]


@app.post("/api/music", status_code=201)
async def create_music(name: str = Form(...), audio: UploadFile = File(...)) -> dict[str, Any]:
    data, mime, ext = await _read_upload(audio, AUDIO_TYPES)
    track = store.add_music(name.strip() or f"track{ext}", data, mime, ext)
    return {"id": track["id"], "name": track["name"]}


@app.delete("/api/music/{music_id}")
async def remove_music(music_id: str) -> dict[str, bool]:
    if not store.delete_music(music_id):
        raise HTTPException(status_code=404, detail="Track not found.")
    return {"deleted": True}


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
    _validate(payload)
    job_id = store.create_job(payload)
    _launch(lambda: pipeline.run_draft(job_id))
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
    }
    _validate(payload)
    job_id = store.create_job(payload)
    _launch(lambda: pipeline.run_draft(job_id))
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

    if patch.on_screen_text is not None:
        scene["on_screen_text"] = patch.on_screen_text.strip()

    if patch.hero_ids is not None:
        known = {h["id"] for h in store.get_heroes(patch.hero_ids)}
        new_ids = [h for h in patch.hero_ids if h in known]
        if new_ids != scene.get("hero_ids"):
            scene["hero_ids"] = new_ids
            scene["needs_image"] = True

    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Scene {index + 1} edited")
    return pipeline.public_scene(job_id, scene)


@app.post("/api/jobs/{job_id}/scenes/{index}/regenerate", status_code=202)
async def regenerate_scene(job_id: str, index: int, body: RegenerateRequest) -> dict[str, Any]:
    job, scenes = _editable_job(job_id)
    if not any(s["index"] == index for s in scenes):
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    if not (body.image or body.voice):
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")

    redo_voice = body.voice and not job["request"].get("narration_audio")
    store.update_job(job_id, status="running", step="regenerate", progress=10)
    _launch(lambda: pipeline.regenerate_scene(
        job_id, index, redo_image=body.image, redo_voice=redo_voice))
    return {"id": job_id, "status": "running"}


@app.post("/api/jobs/{job_id}/render", status_code=202)
async def render_job(job_id: str) -> dict[str, Any]:
    """Finish a reviewed draft, or re-render a finished video after edits."""
    _editable_job(job_id)
    store.update_job(job_id, status="rendering", step="render", progress=74)
    _launch(lambda: pipeline.run_render(job_id))
    return {"id": job_id, "status": "rendering"}


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
