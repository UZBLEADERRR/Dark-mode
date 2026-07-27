"""HTTP layer: hero library, job queue, progress polling, downloads."""

from __future__ import annotations

import asyncio
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, pipeline, store
from .models import CreateJobRequest
from .providers import storage, tts
from .render import video

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 200 * 1024 * 1024
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_TYPES = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm"}

# Rendering is CPU- and memory-hungry; more than a couple at once will thrash a
# small Railway container, so jobs queue behind this instead of all starting.
_job_slots = asyncio.Semaphore(max(1, config.MAX_CONCURRENT_JOBS))
_running: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    store.reset_stale_jobs()
    try:
        await storage.ensure_bucket()
    except Exception:  # noqa: BLE001 - storage is optional
        pass
    yield


app = FastAPI(title="AI YouTube Video Studio", version="1.0.0", lifespan=lifespan)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- helpers -----------------------------------------------------------------

def _safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise HTTPException(status_code=400, detail="Invalid path.")
    return candidate


async def _save_upload(upload: UploadFile, target: Path, allowed: set[str]) -> Path:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix or upload.filename}'. Allowed: {', '.join(sorted(allowed))}",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with target.open("wb") as handle:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="File is larger than 200 MB.")
            handle.write(chunk)
    return target


def _launch(job_id: str) -> None:
    async def runner() -> None:
        async with _job_slots:
            await pipeline.run_job(job_id)

    task = asyncio.create_task(runner())
    _running.add(task)
    task.add_done_callback(_running.discard)


def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result") or {}
    request = job.get("request") or {}
    return {
        "id": job["id"],
        "status": job["status"],
        "step": job.get("step", ""),
        "progress": job.get("progress", 0),
        "topic": request.get("topic", ""),
        "video_format": request.get("video_format", "16:9"),
        "language": request.get("language", "en"),
        "error": job.get("error"),
        "video_url": result.get("video_url"),
        "download_url": result.get("download_url"),
        "subtitle_url": result.get("subtitle_url"),
        "duration": result.get("duration"),
        "title": result.get("title"),
        "scene_count": result.get("scene_count"),
        "metadata": result.get("metadata"),
        "scenes": result.get("scenes"),
        "warnings": result.get("warnings") or [],
        "logs": job.get("logs", [])[-40:],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


# --- pages -------------------------------------------------------------------

@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=404, detail="UI not found.")
    return FileResponse(page)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> RedirectResponse:
    return RedirectResponse(url="/static/favicon.svg")


# --- health ------------------------------------------------------------------

@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "ffmpeg": video.ffmpeg_available(),
        "llm": bool(config.ANTHROPIC_API_KEY),
        "storage": storage.backend(),
        "image_providers": {
            name: config.image_provider_ready(name) for name in ("gemini", "fal", "openai")
        },
        "tts_providers": {
            name: config.tts_provider_ready(name) for name in ("elevenlabs", "openai", "gemini")
        },
        "transcription": bool(config.OPENAI_API_KEY),
        "defaults": {
            "image_provider": config.IMAGE_PROVIDER,
            "tts_provider": config.TTS_PROVIDER,
            "fps": config.FPS,
        },
        "formats": [
            {"id": key, **{k: v for k, v in value.items()}} for key, value in config.FORMATS.items()
        ],
        "languages": [{"id": k, "label": v} for k, v in config.LANGUAGES.items()],
    }


# --- heroes ------------------------------------------------------------------

@app.get("/api/heroes")
async def list_heroes() -> list[dict[str, Any]]:
    return [
        {**hero, "url": f"/api/hero-files/{hero['filename']}"} for hero in store.list_heroes()
    ]


@app.post("/api/heroes", status_code=201)
async def create_hero(
    name: str = Form(...),
    description: str = Form(""),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A hero needs a name.")

    suffix = Path(image.filename or "").suffix.lower() or ".png"
    filename = f"{store.new_id('img')}{suffix}"
    await _save_upload(image, config.HEROES_DIR / filename, IMAGE_TYPES)

    hero = store.add_hero(name, description.strip(), filename)
    return {**hero, "url": f"/api/hero-files/{filename}"}


@app.delete("/api/heroes/{hero_id}")
async def remove_hero(hero_id: str) -> dict[str, bool]:
    hero = store.delete_hero(hero_id)
    if hero is None:
        raise HTTPException(status_code=404, detail="Hero not found.")
    (config.HEROES_DIR / hero["filename"]).unlink(missing_ok=True)
    return {"deleted": True}


@app.get("/api/hero-files/{filename}")
async def hero_file(filename: str) -> FileResponse:
    path = _safe_child(config.HEROES_DIR, filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    return FileResponse(path)


# --- music -------------------------------------------------------------------

@app.get("/api/music")
async def list_music() -> list[dict[str, Any]]:
    return store.list_music()


@app.post("/api/music", status_code=201)
async def create_music(
    name: str = Form(...), audio: UploadFile = File(...)
) -> dict[str, Any]:
    suffix = Path(audio.filename or "").suffix.lower() or ".mp3"
    filename = f"{store.new_id('mus')}{suffix}"
    await _save_upload(audio, config.MUSIC_DIR / filename, AUDIO_TYPES)
    return store.add_music(name.strip() or filename, filename)


@app.delete("/api/music/{music_id}")
async def remove_music(music_id: str) -> dict[str, bool]:
    record = store.delete_music(music_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Track not found.")
    (config.MUSIC_DIR / record["filename"]).unlink(missing_ok=True)
    return {"deleted": True}


# --- jobs --------------------------------------------------------------------

def _validate(request: dict[str, Any]) -> None:
    if not config.ANTHROPIC_API_KEY:
        raise HTTPException(
            status_code=400, detail="ANTHROPIC_API_KEY is not set — the AI skills cannot run."
        )
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
    _launch(job_id)
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
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """Storyboard a voice-over the user already has, instead of generating one."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=400,
            detail="Uploaded voice-overs need an OPENAI_API_KEY — the subtitles are timed "
            "from a transcription of your audio.",
        )

    suffix = Path(audio.filename or "").suffix.lower() or ".mp3"
    staged = config.DATA_DIR / "uploads" / f"{store.new_id('narr')}{suffix}"
    await _save_upload(audio, staged, AUDIO_TYPES)

    payload = {
        "topic": topic.strip(),
        "video_format": video_format,
        "language": language,
        "art_style": art_style,
        "tone": tone,
        "hero_ids": [h for h in hero_ids.split(",") if h.strip()],
        "image_provider": image_provider,
        "music_id": music_id or None,
        "subtitle_style": subtitle_style,
        "burn_subtitles": burn_subtitles,
        "narration_audio": str(staged),
        "target_seconds": 0,
    }
    _validate(payload)
    job_id = store.create_job(payload)
    _launch(job_id)
    return {"id": job_id, "status": "queued"}


@app.get("/api/jobs")
async def list_jobs(limit: int = 30) -> list[dict[str, Any]]:
    return [_job_payload(job) for job in store.list_jobs(min(limit, 100))]


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return _job_payload(job)


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, bool]:
    if not store.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    shutil.rmtree(config.PROJECTS_DIR / job_id, ignore_errors=True)
    return {"deleted": True}


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str) -> FileResponse:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    folder = config.PROJECTS_DIR / job_id
    videos = sorted(folder.glob("*.mp4")) if folder.exists() else []
    videos = [v for v in videos if not v.name.startswith("clip_")]
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
