"""HTTP layer: hero library, job queue, scene editing, progress polling, downloads."""

from __future__ import annotations

import asyncio
import io
import mimetypes
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from PIL import Image as PILImage

from . import config, keys, pgstore, pipeline, planner, skills, store
from .models import (
    ApiKeyIn,
    FlowFail,
    FlowMode,
    FlowPrompt,
    ApiKeyPatch,
    BrandKit,
    ChatTurn,
    LengthAsk,
    CreateJobRequest,
    HeroPatch,
    JobPatch,
    ModelSettings,
    MusicSwap,
    PlanIn,
    PlanPatch,
    ProfilePatch,
    PublishRequest,
    RegenerateRequest,
    RepurposeRequest,
    SceneInsert,
    SceneOrder,
    ScenePatch,
    ScriptNote,
    ShortCut,
    ShortsAll,
    ShotIn,
    TranslateRequest,
)
from .providers import catalog, flowext, images, storage, tts, youtube
from .skills import llm, strategist
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
    """Provision the bucket without being able to stop the server starting.

    Quiet about succeeding, never about failing: a bucket that was not created
    means every picture and every voice clip goes to the database instead, and
    the finished video has nowhere to live at all. That used to be swallowed
    here, and the first anyone heard of it was "Bucket not found" on a render
    that had already been paid for.
    """
    try:
        if not await storage.ensure_bucket():
            print(f"[sarideo] Supabase Storage: {storage.bucket_problem()}", flush=True)
    except Exception as exc:  # noqa: BLE001 - remote storage is optional
        print(f"[sarideo] Supabase Storage tekshirilmadi: {exc}", flush=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start up. Nothing in here may prevent the server from accepting requests.

    A database that cannot be reached is a configuration problem, and the only
    place a configuration problem can be explained is a page the user can open.
    If it kills startup instead, the platform reports a failed healthcheck, rolls
    back, and loops — and the actual reason is buried in a stack trace nobody
    asked for. So every step here is allowed to fail, and what failed is said on
    the settings page.
    """
    store.init()
    resumable: list[str] = []
    try:
        # A render is server-side work: closing the browser never stopped it, and
        # now neither does the container being replaced under it. Anything
        # interrupted mid-render is picked straight back up.
        resumable = store.recover_jobs()
        # Model and voice choices made in the UI are stored, not exported to the
        # environment, so they have to be loaded back before the first request.
        config.set_model_overrides(store.get_setting(MODELS_KEY) or {})
        config.set_voice_overrides(store.get_setting(VOICES_KEY) or {})
        # Same reasoning for the image provider: switching to `flow` from the
        # library must survive a restart, or it is a setting you re-apply every
        # deploy and eventually stop trusting.
        config.set_image_provider(store.get_setting(IMAGE_PROVIDER_KEY) or "")
        config.set_llm_provider(store.get_setting(LLM_PROVIDER_KEY) or "")
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        print(f"[sarideo] Baza bilan ishlanmadi: {pgstore.explain(exc)}", flush=True)
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

    # Plans are promises about the future, so something has to be awake to keep
    # them. Started here, after the server is answering, and it is allowed to fail
    # for ever without taking anything else down.
    plans = asyncio.create_task(planner.run_forever(_launch))
    _running.add(plans)
    plans.add_done_callback(_running.discard)
    try:
        yield
    finally:
        plans.cancel()


app = FastAPI(title="Sarideo", version="2.0.0", lifespan=lifespan)

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


# Jobs holding or waiting for a slot, oldest first. The semaphore already made
# work queue instead of thrashing the container, but it queued invisibly: a
# render that was second in line said "rendering" and then did nothing for ten
# minutes, which is indistinguishable from a render that has hung.
_queue: list[str] = []


def queue_place(job_id: str) -> int:
    """Where this job is in the waiting line. 0 = being worked on, 1 = next."""
    try:
        place = _queue.index(job_id)
    except ValueError:
        return 0
    return max(0, place - config.MAX_CONCURRENT_JOBS + 1)


def _launch(coro_factory, job_id: str | None = None, *, on_start=None) -> None:
    # Enrolled here rather than inside the task: the task body does not run until
    # the loop comes back round, and the caller needs to be able to tell the user
    # their place in the queue in the same response.
    if job_id:
        _queue.append(job_id)

    async def runner() -> None:
        try:
            async with _job_slots:
                # Called once the slot is actually held, which is the moment the
                # work starts rather than the moment it was asked for.
                if on_start:
                    on_start()
                await coro_factory()
        finally:
            if job_id and job_id in _queue:
                _queue.remove(job_id)

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
        # 0 while it is being worked on, 1 when it is next. A project waiting its
        # turn behind another render is doing nothing, and saying so is the
        # difference between patience and thinking the app has frozen.
        "queue_place": queue_place(job["id"]),
        "kind": request.get("kind", "video"),
        "topic": request.get("topic", ""),
        "video_format": request.get("video_format", "16:9"),
        "language": request.get("language", "en"),
        "auto_render": request.get("auto_render", True),
        "uses_uploaded_audio": bool(request.get("narration_audio")),
        "caption_style": subs.resolve_style(
            request.get("caption_style") or request.get("subtitle_style", "bold")),
        "burn_subtitles": bool(request.get("burn_subtitles", True)),
        # What this project was started with, and what it would actually use
        # today. They differ whenever the app-wide choice moved on without it,
        # which is the whole reason the editor offers to change it.
        # How many scenes are showing a stand-in. Nothing else can tell: the file
        # exists, so "18/18" looks finished.
        "placeholders": len(pipeline.placeholder_scenes(scenes)),
        "image_provider": request.get("image_provider") or "",
        "image_provider_now": (request.get("image_provider")
                               or config.IMAGE_PROVIDER).lower(),
        "music_id": request.get("music_id") or "",
        "music_start": float(request.get("music_start") or 0.0),
        "error": job.get("error") or None,
        "video_url": result.get("video_url"),
        "download_url": result.get("download_url"),
        "subtitle_url": result.get("subtitle_url"),
        "duration": result.get("duration"),
        "title": result.get("title"),
        "scene_count": result.get("scene_count") or len(scenes),
        # Only a subtitling job has one, and for that job it is the count that
        # means something.
        "caption_count": result.get("caption_count"),
        "metadata": result.get("metadata"),
        "thumbnails": result.get("thumbnails") or [],
        # Where it went, once it has gone somewhere. The gallery offers to open it
        # rather than to publish it again.
        "youtube": result.get("youtube") or None,
        "transcript": result.get("transcript") or [],
        "scenes": [pipeline.public_scene(job["id"], s) for s in scenes] if with_scenes else None,
        # What is still missing, so a job that stopped halfway can say how far
        # it got rather than only that it failed.
        "progress_detail": pipeline.unfinished(
            scenes, uploaded_audio=bool(request.get("narration_audio"))) if scenes else None,
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


def _database() -> dict[str, Any]:
    """What is holding the library, and whether it will still be there tomorrow."""
    files = storage.backend()
    if not pgstore.enabled():
        return {
            "backend": "sqlite", "ok": True, "durable": False, "files": files,
            "note": "Hamma narsa shu konteynerda — deploy qilinsa o'chadi. "
                    "Saqlanishi uchun DATABASE_URL'ni Supabase'ga ulang.",
        }
    ok, detail = pgstore.health()
    if not ok:
        return {"backend": "postgres", "ok": False, "durable": False, "files": files,
                "note": f"Bazaga ulanmadi: {detail}"}
    if files != "supabase":
        # Half a setup is worth naming: the rows are safe and the pictures are
        # not, which looks like it works right up until the container restarts.
        return {
            "backend": "postgres", "ok": True, "durable": False, "files": files,
            "note": "Baza saqlanadi, ammo rasm va ovoz fayllari konteynerda. "
                    "STORAGE_BACKEND=supabase qo'ying — shunda yarim tayyor "
                    "loyihalar ham to'liq saqlanadi.",
        }
    trouble = storage.bucket_problem()
    if trouble:
        # The bucket is configured and not working. Rows and media still go to
        # the database, so nothing made is lost — but the finished video has
        # nowhere permanent to sit, and that is worth saying out loud.
        return {"backend": "postgres", "ok": True, "durable": False, "files": files,
                "bucket": trouble,
                "note": f"Rasm va ovozlar bazaga saqlanmoqda. Bucket ishlamayapti: "
                        f"{trouble}"}
    return {"backend": "postgres", "ok": True, "durable": True, "files": files,
            "note": "Hamma narsa saqlanadi — herolar, loyihalar, rasm va ovozlar."}


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
        "database": _database(),
        # The pacing that applies to the provider actually configured — a number
        # that belongs to some other provider's key would only mislead.
        "tts_rate_limit": config.tts_rate_limit(config.TTS_PROVIDER),
        # Read from the list rather than repeated here. It was repeated, and
        # `manual` — added later — never reached the composer's picker because
        # of it, so the one mode that needs no key was the hardest to find.
        "image_providers": {n: config.image_provider_ready(n)
                            for n in config.IMAGE_PROVIDERS},
        # How many scene prompts are sitting in the queue right now. Zero is the
        # normal answer; a number that is not going down is a browser that is not
        # open, and that is worth being able to see from the health panel.
        "flow_waiting": len(store.list_image_tasks()),
        "tts_providers": {n: config.tts_provider_ready(n)
                          for n in ("elevenlabs", "openai", "gemini")},
        "transcription": config.has_key("openai"),
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
        "can_dub": config.has_key("gemini") or config.has_key("openai"),
        # Subtitling asks less of a deployment than anything else here: it needs
        # something that can listen, and nothing that can speak or draw.
        "can_subtitle": config.has_key("gemini") or config.has_key("openai"),
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
            {"id": k, **v,
             "caption": config.caption_budget(v["width"], v["height"]),
             # The same budget for a script written in square characters. Both
             # are computed here so the editor's preview can pick between them
             # without a second copy of the arithmetic in the browser.
             "caption_dense": config.caption_budget(v["width"], v["height"], "ko")}
            for k, v in config.FORMATS.items()
        ],
        "dense_scripts": sorted(config.DENSE_SCRIPTS),
        "languages": [{"id": k, "label": v} for k, v in config.LANGUAGES.items()],
    }


# ── heroes (stored in the database, blob and all) ─────────────────────────────

def _hero_out(hero: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": hero["id"],
        "name": hero["name"],
        "description": hero.get("description", ""),
        # A character that has been given a voice speaks its own lines; one that
        # has not is read by the narrator, which is how it has always worked.
        "voice_id": hero.get("voice_id") or "",
        "tts_provider": hero.get("tts_provider") or "",
        "url": f"/api/heroes/{hero['id']}/image",
    }


@app.get("/api/heroes")
async def list_heroes() -> list[dict[str, Any]]:
    return [_hero_out(h) for h in store.list_heroes()]


@app.post("/api/heroes", status_code=201)
async def create_hero(
    name: str = Form(...),
    description: str = Form(""),
    voice_id: str = Form(""),
    tts_provider: str = Form(""),
    image: UploadFile = File(...),
) -> dict[str, Any]:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A hero needs a name.")
    data, mime, ext = await _read_upload(image, IMAGE_TYPES)
    hero = store.add_hero(name, description.strip(), data, mime, ext,
                          voice_id.strip(), tts_provider.strip().lower())
    return _hero_out(hero)


@app.patch("/api/heroes/{hero_id}")
async def edit_hero(hero_id: str, patch: HeroPatch) -> dict[str, bool]:
    if not store.update_hero(hero_id, name=patch.name, description=patch.description,
                             voice_id=patch.voice_id, tts_provider=patch.tts_provider):
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


# ── your own channels, and the assistant that talks about them ────────────────

CHAT_KEY = "chat.log"
# Enough for the assistant to hold a thread without the row growing without
# bound. A conversation older than this has been superseded by the video it led to.
CHAT_KEEP = 60


def _profile_out(profile: dict[str, Any]) -> dict[str, Any]:
    return {"id": profile["id"], "platform": profile.get("platform", ""),
            "handle": profile.get("handle", ""), "summary": profile.get("summary", ""),
            "niche": profile.get("niche", ""), "audience": profile.get("audience", ""),
            "language": profile.get("language", ""), "pillars": profile.get("pillars", ""),
            "style": profile.get("style", ""),
            "url": f"/api/profiles/{profile['id']}/image"}


@app.get("/api/profiles")
async def list_profiles(platform: str = "") -> list[dict[str, Any]]:
    return [_profile_out(p) for p in store.list_profiles(platform.strip().lower())]


@app.post("/api/profiles", status_code=201)
async def create_profile(
    image: UploadFile = File(...),
    platform: str = Form(""),
    handle: str = Form(""),
) -> dict[str, Any]:
    """Keep a screenshot of one of your channels, and what can be read off it.

    Read once, here, rather than on every question about it: the reading is a
    vision call and the answer does not change between one conversation and the
    next. A reading that fails is not a failure — the screenshot is kept anyway
    and the person can say what it is themselves.
    """
    data, mime, ext = await _read_upload(image, IMAGE_TYPES)
    where = platform.strip().lower()
    if where and where not in strategist.PLATFORMS:
        where = "other"
    seen = await strategist.read_profile(data, mime, where)
    # Everything that was read is kept, not just the prose. The specifics are
    # what the assistant is held to when it proposes something; with only a
    # summary in front of it, a model has nothing particular to be faithful to
    # and answers with something that would suit any channel.
    return _profile_out(store.add_profile(
        where or seen.get("platform") or "other",
        handle.strip() or seen.get("handle") or "",
        seen.get("summary") or "", data, mime, ext,
        niche=seen.get("niche", ""), audience=seen.get("audience", ""),
        language=seen.get("language", ""), pillars=seen.get("pillars", ""),
        style=seen.get("style", "")))


@app.get("/api/profiles/{profile_id}/image")
async def profile_image(profile_id: str) -> Response:
    found = store.get_profile_image(profile_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    data, mime, _ext = found
    return Response(content=data, media_type=mime,
                    headers={"Cache-Control": "public, max-age=31536000"})


@app.patch("/api/profiles/{profile_id}")
async def edit_profile(profile_id: str, body: ProfilePatch) -> dict[str, bool]:
    if not store.update_profile(
            profile_id, handle=body.handle, summary=body.summary,
            niche=body.niche, audience=body.audience, language=body.language,
            pillars=body.pillars, style=body.style):
        raise HTTPException(status_code=404, detail="Profile not found.")
    return {"updated": True}


@app.delete("/api/profiles/{profile_id}")
async def remove_profile(profile_id: str) -> dict[str, bool]:
    if not store.delete_profile(profile_id):
        raise HTTPException(status_code=404, detail="Profile not found.")
    return {"deleted": True}


@app.post("/api/advise/length")
async def advise_length(body: LengthAsk) -> dict[str, Any]:
    """Roughly how long this subject should run, and whether it is a Short.

    The two decisions that have to be made before anything can be made, and the
    two the composer used to leave to a number box with a default in it. Read
    only — nothing is started, nothing is saved.
    """
    topic = body.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="Mavzu bo'sh.")
    if not config.llm_ready():
        raise HTTPException(
            status_code=400,
            detail="Tavsiya uchun AI kaliti kerak — kutubxonadagi \"API kalitlari\" "
                   "bo'limiga Gemini yoki Anthropic kalitini qo'shing.")
    try:
        return await skills.advise_length(
            topic, profiles=store.list_profiles(), language=body.language or "")
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/chat")
async def chat_history() -> dict[str, Any]:
    return {"messages": store.get_setting(CHAT_KEY) or []}


@app.delete("/api/chat")
async def clear_chat() -> dict[str, bool]:
    store.set_setting(CHAT_KEY, [])
    return {"cleared": True}


@app.post("/api/chat")
async def chat(body: ChatTurn) -> dict[str, Any]:
    """One turn. May end in a video being started, which is the point of it.

    The assistant is only allowed to start a render once it has everything it
    needs — that rule lives in the skill, which turns an incomplete request back
    into a question. So this endpoint either says something, or says something
    *and* has a job id: it never starts a job it had to guess at.
    """
    said = body.message.strip()
    if not said:
        raise HTTPException(status_code=400, detail="Xabar bo'sh.")
    if not config.llm_ready():
        raise HTTPException(
            status_code=400,
            detail="Suhbat uchun AI kaliti kerak — kutubxonadagi \"API kalitlari\" "
                   "bo'limiga Gemini yoki Anthropic kalitini qo'shing.")

    history = store.get_setting(CHAT_KEY) or []
    try:
        answer = await skills.chat(
            message=said, history=history,
            profiles=store.list_profiles(), heroes=store.list_heroes(),
            languages=list(config.LANGUAGES),
        )
    except llm.LLMError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    job_id = None
    if answer["create"]:
        # Straight down the ordinary create path — same validation, same queue,
        # same draft-then-review flow as the form. A video the assistant started
        # is not a different kind of video.
        payload = {k: v for k, v in answer["create"].items() if v is not None}
        payload.setdefault("auto_render", False)
        request = CreateJobRequest(**payload)
        made = await create_job(request)
        job_id = made["id"]

    turn = [{"role": "user", "text": said},
            {"role": "bot", "text": answer["reply"], "ideas": answer["ideas"],
             "asks": answer["asks"], "job_id": job_id}]
    store.set_setting(CHAT_KEY, (history + turn)[-CHAT_KEEP:])
    return {**answer, "job_id": job_id}


# ── plans: a video asked for in advance ───────────────────────────────────────

def _plan_out(plan: dict[str, Any]) -> dict[str, Any]:
    job = store.get_job(plan.get("job_id") or "") if plan.get("job_id") else None
    request = plan.get("request") or {}
    return {
        "id": plan["id"],
        "title": plan.get("title") or request.get("topic", ""),
        "topic": request.get("topic", ""),
        "channel": plan.get("channel", ""),
        # Everything the composer needs to be filled in from this plan, so
        # activating one is a form that is already answered rather than a note
        # telling you what to type.
        "compose": {
            "topic": request.get("topic", ""),
            "video_format": request.get("video_format", "9:16"),
            "target_seconds": request.get("target_seconds", 45),
            "language": request.get("language", "uz"),
            "tone": request.get("tone", ""),
            "art_style": request.get("art_style", ""),
            "action": request.get("action", ""),
            "hero_ids": request.get("hero_ids") or [],
            "animate_actors": bool(request.get("animate_actors")),
            "music_id": request.get("music_id") or "",
            "image_provider": request.get("image_provider") or "",
        },
        "image_provider": request.get("image_provider") or "",
        "publish_at": plan.get("publish_at", ""),
        "lead_minutes": plan.get("lead_minutes", 0),
        "privacy": plan.get("privacy", "public"),
        "approve": bool(plan.get("approve")),
        # What was chosen, and what that works out to for this slot.
        "batch_mode": plan.get("batch") or "auto",
        "status": plan.get("status", ""),
        "job_id": plan.get("job_id", ""),
        "video_url": plan.get("video_url", ""),
        "error": plan.get("error", ""),
        "video_format": (plan.get("request") or {}).get("video_format", ""),
        # Whether this one is taking the cheap slow road, and how far off it is.
        "batch": planner.wants_batch(plan),
        "hours_left": round(planner.hours_until(plan.get("publish_at", "")), 1),
        "note": planner.describe(plan),
        "job_status": job["status"] if job else "",
        "job_progress": job.get("progress", 0) if job else 0,
        "created_at": plan.get("created_at"),
    }


@app.get("/api/plans")
async def list_plans(limit: int = 60) -> list[dict[str, Any]]:
    return [_plan_out(p) for p in store.list_plans(min(limit, 200))]


@app.post("/api/plans", status_code=201)
async def create_plan(body: PlanIn) -> dict[str, Any]:
    when = body.publish_at.strip()
    # No slot means the shelf, not a mistake. A dated plan still has to be in
    # the future — a schedule that has already passed will never fire.
    if when and planner.hours_until(when) <= 0:
        raise HTTPException(status_code=400,
                            detail="Chiqish vaqti o'tib ketgan — kelajakdagi vaqtni tanlang.")
    request = {
        "topic": body.topic.strip(),
        "video_format": body.video_format,
        "target_seconds": body.target_seconds,
        "language": body.language,
        "hero_ids": body.hero_ids,
        "animate_actors": body.animate_actors,
        "music_id": body.music_id or None,
        "auto_render": True,
    }
    for key, value in (("tone", body.tone), ("art_style", body.art_style),
                       ("action", body.action)):
        if value.strip():
            request[key] = value.strip()
    wanted = body.image_provider.strip().lower()
    if wanted:
        if wanted not in config.IMAGE_PROVIDERS:
            raise HTTPException(status_code=400,
                                detail=f"'{wanted}' — bunday rasm provayderi yo'q.")
        request["image_provider"] = wanted
    # Only a dated plan is checked against what is configured right now. An idea
    # written today may well be built next month with different keys, and
    # refusing to write it down for that reason would be absurd.
    if when:
        _validate(request)
    plan = store.add_plan(
        title=body.title.strip() or body.topic.strip(), request=request,
        publish_at=when, lead_minutes=body.lead_minutes,
        privacy=body.privacy, approve=body.approve, batch=body.batch,
        channel=body.channel.strip(), status="planned" if when else "idea")
    return _plan_out(plan)


@app.post("/api/plans/{plan_id}/activate")
async def activate_plan(plan_id: str) -> dict[str, Any]:
    """Take an idea off the shelf: mark it used and hand back the composer's answers.

    Deliberately does not start anything. The point of the shelf is that you
    look at the idea again before making it — the plan fills the form in and
    then gets out of the way.
    """
    plan = store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Reja topilmadi.")
    if plan.get("status") not in {"idea", "used"}:
        raise HTTPException(
            status_code=409,
            detail="Bu reja vaqtga qo'yilgan — o'zi tayyorlanadi, "
                   "hoziroq boshlash uchun «Hoziroq boshlash» ni bosing.")
    store.update_plan(plan_id, status="used")
    return _plan_out(store.get_plan(plan_id) or {})


@app.patch("/api/plans/{plan_id}")
async def edit_plan(plan_id: str, body: PlanPatch) -> dict[str, Any]:
    if store.get_plan(plan_id) is None:
        raise HTTPException(status_code=404, detail="Reja topilmadi.")
    if body.publish_at and planner.hours_until(body.publish_at) <= 0:
        raise HTTPException(status_code=400, detail="Chiqish vaqti o'tib ketgan.")
    store.update_plan(plan_id, **body.model_dump(exclude_none=True))
    return _plan_out(store.get_plan(plan_id) or {})


@app.delete("/api/plans/{plan_id}")
async def remove_plan(plan_id: str) -> dict[str, bool]:
    if not store.delete_plan(plan_id):
        raise HTTPException(status_code=404, detail="Reja topilmadi.")
    return {"deleted": True}


@app.post("/api/plans/{plan_id}/approve", status_code=202)
async def approve_plan(plan_id: str) -> dict[str, Any]:
    """Say yes. The video goes up, timed to the slot the plan asked for."""
    try:
        made = await planner.publish_plan(plan_id)
    except planner.PlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {**made, "plan": _plan_out(store.get_plan(plan_id) or {})}


@app.post("/api/plans/{plan_id}/start", status_code=202)
async def start_plan_now(plan_id: str) -> dict[str, Any]:
    """Build it now rather than waiting for the lead time."""
    plan = store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Reja topilmadi.")
    if plan["status"] != "planned":
        raise HTTPException(status_code=409,
                            detail="Bu reja allaqachon boshlangan.")
    await planner.start_plan(plan, _launch)
    return _plan_out(store.get_plan(plan_id) or {})


# ── publishing to your own channel ────────────────────────────────────────────

@app.get("/api/youtube")
async def youtube_status() -> dict[str, Any]:
    return youtube.status()


@app.get("/api/youtube/auth")
async def youtube_auth() -> dict[str, str]:
    """Where to send the browser. Returned rather than redirected to, because the
    app is a single page and a redirect would take it off screen."""
    try:
        return {"url": youtube.auth_url()}
    except youtube.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/youtube/callback", include_in_schema=False)
async def youtube_callback(code: str = "", error: str = "") -> HTMLResponse:
    """Where Google sends the browser back. A page, not JSON — a person is looking."""
    if error or not code:
        said = error or "Google kod qaytarmadi."
        return HTMLResponse(_closing_page(f"Ulanmadi: {said}", ok=False))
    try:
        who = await youtube.exchange(code)
    except youtube.YouTubeError as exc:
        return HTMLResponse(_closing_page(f"Ulanmadi: {exc}", ok=False))
    name = who.get("channel_title") or "kanalingiz"
    return HTMLResponse(_closing_page(f"{name} ulandi. Bu oynani yopsangiz bo'ladi."))


def _closing_page(message: str, ok: bool = True) -> str:
    colour = "#3ddc91" if ok else "#ff5c47"
    return f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sarideo · YouTube</title>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;
  background:#08090c;color:#eef1f7;font:15px/1.6 -apple-system,BlinkMacSystemFont,sans-serif">
<div style="max-width:30rem;padding:28px;text-align:center">
  <div style="width:44px;height:44px;margin:0 auto 14px;border-radius:99px;
    background:{colour};opacity:.16"></div>
  <p style="color:{colour};font-weight:600;margin:0 0 8px">{"Tayyor" if ok else "Xatolik"}</p>
  <p style="margin:0;color:#7c8497">{message}</p>
</div>
<script>
  // The app opened this in another tab and is waiting to hear back.
  try {{ window.opener && window.opener.postMessage(
    {{ sarideo: 'youtube', ok: {str(ok).lower()} }}, '*'); }} catch (e) {{}}
  {"setTimeout(() => window.close(), 1600);" if ok else ""}
</script>
</body>"""


@app.delete("/api/youtube")
async def youtube_disconnect() -> dict[str, bool]:
    youtube.disconnect()
    return {"disconnected": True}


@app.post("/api/jobs/{job_id}/publish", status_code=202)
async def publish_job(job_id: str, body: PublishRequest) -> dict[str, Any]:
    """Put a finished video on the connected channel.

    Only ever from a finished render, and only when asked: this is the one action
    in the app that other people can see, so it is never a side effect of
    something else.
    """
    job = _get_job_or_404(job_id)
    if job["status"] != "done":
        raise HTTPException(status_code=400,
                            detail="Faqat tayyor video joylanadi — avval render qiling.")
    if not youtube.connected():
        raise HTTPException(status_code=400,
                            detail="YouTube kanali ulanmagan — Kutubxonada ulang.")

    result = job.get("result") or {}
    meta = (result.get("metadata") or {}).get("youtube") or {}
    local = await pipeline.finished_file(job_id)
    if local is None:
        raise HTTPException(
            status_code=409,
            detail="Video fayli topilmadi — konteyner o'chgan bo'lsa «Qayta render» qiling.")

    title = (body.title or meta.get("title") or result.get("title") or "Video").strip()
    description = body.description if body.description is not None else meta.get("description", "")
    tags = body.tags if body.tags is not None else (meta.get("tags") or [])

    try:
        made = await youtube.upload(
            local, title=title, description=description or "", tags=tags,
            privacy=body.privacy, publish_at=body.publish_at or None,
            language=job["request"].get("language", ""),
        )
    except youtube.YouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if body.with_thumbnail:
        shot = await pipeline.thumbnail_file(job_id)
        if shot is not None:
            made["thumbnail_set"] = await youtube.set_thumbnail(made["id"], shot)

    # Kept on the job, so the gallery can say "this one is up" rather than
    # offering to publish it again.
    store.update_job(job_id, result={**result, "youtube": made},
                     log=f"YouTube'ga joylandi: {made['url']}")
    return made


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
IMAGE_PROVIDER_KEY = "image_provider"
# Which Flow project the agent is pointed at. Kept here as well as in the agent's
# own environment so the field shows the right thing after either restarts.
FLOW_PROJECT_KEY = "flow_project"
LLM_PROVIDER_KEY = "llm_provider"
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
        # Which model writes the script is a choice, not a consequence of which
        # key happens to exist — so it is offered as one, with the keyless
        # options marked rather than hidden.
        "text_providers": [
            {"id": name, "ready": config.has_key(name)}
            for name in config.LLM_PROVIDERS
        ],
        "text_provider": config.llm_provider(),
        # "auto" is a real answer, and different from whichever provider it
        # currently resolves to — so the page can show it as chosen.
        "text_choice": config.LLM_PROVIDER,
        # Same for the pictures, and here it matters more: a choice stored from
        # the Flow switch outranks IMAGE_PROVIDER at startup, so somebody who
        # sets the variable and redeploys can watch it be ignored with nothing
        # on screen to say why. Both are reported, and the page says so when
        # they disagree.
        "image_providers": [
            {"id": name, "ready": config.image_provider_ready(name)}
            for name in config.IMAGE_PROVIDERS
        ],
        "image_provider": config.IMAGE_PROVIDER,
        "image_env": config.IMAGE_PROVIDER_ENV,
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
    if body.text_provider is not None:
        # Stored like the models beside it, so choosing GPT survives a restart
        # rather than being a thing you set again after every deploy.
        config.set_llm_provider(body.text_provider)
        store.set_setting(LLM_PROVIDER_KEY, config.LLM_PROVIDER)
    if body.image_provider is not None:
        # An empty string is a real answer here — "stop overriding, use whatever
        # the environment says" — and it is the only way back out of a choice
        # made months ago from a switch the user has forgotten about.
        config.set_image_provider(body.image_provider)
        store.set_setting(IMAGE_PROVIDER_KEY,
                          config.IMAGE_PROVIDER if body.image_provider else "")
    return _models_out()


@app.get("/api/models/available")
async def available_models(provider: str) -> dict[str, Any]:
    """What this key may actually call — asked of the provider, not guessed."""
    return await catalog.list_models(provider)


# ── API keys ──────────────────────────────────────────────────────────────────

def _key_out(row: dict[str, Any], secret: str = "") -> dict[str, Any]:
    """One key as the page may see it: everything except the key.

    The secret is never returned, not even to the client that just sent it. What
    is returned is its ends and its length, and those are worth returning: when a
    provider says a key is invalid, the only useful question is whether the thing
    stored here is the thing on the dashboard, and `AQ.A…54SQ · 53 belgi` answers
    it at a glance. A partial paste is a different length; a stale key has
    different ends. Neither is visible from a name somebody typed.
    """
    left = keys.cooling(row)
    secret = secret or ""
    return {
        "id": row.get("id", ""),
        "provider": row.get("provider", ""),
        "label": row.get("label", ""),
        "mask": keys.mask(secret),
        "length": len(secret),
        "enabled": bool(row.get("enabled")),
        "uses": int(row.get("uses") or 0),
        "fails": int(row.get("fails") or 0),
        "ok_at": row.get("ok_at", ""),
        "failed_at": row.get("failed_at", ""),
        "cooldown_seconds": round(left),
        "last_error": row.get("last_error", ""),
        "created_at": row.get("created_at", ""),
    }


def _label_for(label: str, secret: str) -> str:
    """The name to store, which is never the key itself.

    With no name shown on the card there was nothing to recognise a key by, so
    people pasted the key into the name box — and then the whole key sat on the
    screen, in a library that gets opened in front of other people and
    screenshotted when something goes wrong. The card shows the masked ends now,
    so a name that is really the key is dropped rather than displayed.
    """
    text = (label or "").strip()
    if not secret or not text:
        return text
    # A name that *contains* the key is dropped whatever its length. A name that
    # is merely part of the key has to be long enough to be one — "AQ" is a
    # perfectly good name for an account, and it is also the first two characters
    # of every Google key issued this year.
    if secret in text or (len(text) >= 12 and text in secret):
        return ""
    return text


def _keys_out() -> dict[str, Any]:
    rows = store.list_keys()
    # The secrets are read here and never leave: `_key_out` turns each one into
    # four characters and a number. Reading them provider by provider keeps
    # `list_keys` what it is — the query that cannot leak a key.
    held = {r.get("id"): r.get("secret", "")
            for name in keys.PROVIDERS for r in keys.stored(name)}
    return {
        "providers": [
            {
                **keys.health(name),
                "env_var": env,
                # Which provider each stage will actually call, so a page can say
                # what a missing key would break rather than just listing names.
                "keys_list": [_key_out(r, held.get(r.get("id"), ""))
                              for r in rows if r.get("provider") == name],
            }
            for name, env in keys.PROVIDERS.items()
        ],
        "in_use": {
            "text": config.llm_provider(),
            "image": config.IMAGE_PROVIDER,
            "tts": config.TTS_PROVIDER,
        },
    }


@app.get("/api/keys")
async def get_keys() -> dict[str, Any]:
    return _keys_out()


@app.post("/api/keys", status_code=201)
async def add_key(body: ApiKeyIn) -> dict[str, Any]:
    # Cleaned, not judged. Whether the key works is the provider's answer, given
    # when it is tested or used — not something to guess from its shape here.
    secret = keys.clean(body.secret)
    if not secret:
        raise HTTPException(status_code=400, detail="Kalit bo'sh.")
    # The one thing worth judging: a key that cannot be put in a header will fail
    # every call with an error about encoding rather than about the key, so it is
    # refused now, while the person who pasted it is still looking at the form.
    wrong = keys.unsendable(secret)
    if wrong:
        raise HTTPException(status_code=400, detail=wrong)
    existing = {r.get("secret") for r in keys.stored(body.provider)}
    if secret in existing:
        raise HTTPException(status_code=409, detail="Bu kalit allaqachon qo'shilgan.")
    row = store.add_key(body.provider, secret, _label_for(body.label, secret))
    return {"key": _key_out(row, secret), "keys": _keys_out()}


@app.patch("/api/keys/{key_id}")
async def patch_key(key_id: str, body: ApiKeyPatch) -> dict[str, Any]:
    if not store.get_key(key_id):
        raise HTTPException(status_code=404, detail="Kalit topilmadi.")
    fields: dict[str, Any] = {}
    secret = keys.clean(body.secret or "")
    if body.secret is not None and secret:
        wrong = keys.unsendable(secret)
        if wrong:
            raise HTTPException(status_code=400, detail=wrong)
        # Same rule as adding one. Two cards holding the same key would take two
        # turns in the rotation for one allowance, and a refusal recorded against
        # either of them would be written to whichever was found first.
        twice = any(r.get("secret") == secret and r.get("id") != key_id
                    for r in keys.stored(store.get_key(key_id)["provider"]))
        if twice:
            raise HTTPException(status_code=409, detail="Bu kalit allaqachon qo'shilgan.")
        fields["secret"] = secret
        # A new secret has its own allowance, so the old one's cooldown and
        # remembered error do not apply to it. The failure count goes too: eight
        # failures belonged to the key that was replaced.
        fields["cooldown_until"] = ""
        fields["last_error"] = ""
        fields["fails"] = 0
    if body.label is not None:
        fields["label"] = _label_for(body.label, secret)
    if body.enabled is not None:
        fields["enabled"] = body.enabled
    if body.clear_cooldown:
        fields["cooldown_until"] = ""
        fields["last_error"] = ""
    if fields:
        store.update_key(key_id, **fields)
    row = store.get_key(key_id) or {}
    kept = next((r.get("secret", "") for r in keys.stored(row.get("provider", ""))
                 if r.get("id") == key_id), "")
    return {"key": _key_out(row, kept), "keys": _keys_out()}


@app.post("/api/keys/{key_id}/test")
async def test_key(key_id: str) -> dict[str, Any]:
    """Try the key against its provider and say what happened.

    The result is written back the same way a real call would write it, so a key
    that fails here is benched for real work too — testing and using cannot be
    allowed to disagree about whether a key works.
    """
    row = store.get_key(key_id)
    if not row:
        raise HTTPException(status_code=404, detail="Kalit topilmadi.")
    provider = row["provider"]
    secret = next((r.get("secret", "") for r in keys.stored(provider)
                   if r.get("id") == key_id), "")
    ok, detail, hold = await keys.probe(provider, secret)
    if ok:
        keys.bless(provider, secret)
    else:
        # A key the provider rejected is benched; one we simply could not reach is
        # only written down, because the network is not the key's fault.
        if hold > 0:
            keys.penalise(provider, secret, seconds=hold, body=detail)
        store.update_key(key_id, last_error=detail)
    return {"ok": ok, "detail": detail,
            "key": _key_out(store.get_key(key_id) or {}, secret), "keys": _keys_out()}


@app.delete("/api/keys/{key_id}")
async def remove_key(key_id: str) -> dict[str, Any]:
    if not store.delete_key(key_id):
        raise HTTPException(status_code=404, detail="Kalit topilmadi.")
    return {"deleted": key_id, "keys": _keys_out()}


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
        raise HTTPException(
            status_code=400,
            detail=f"No {provider} API key — add one in the library, under \"API kalitlari\".")
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
    # The create form sends all of these whichever way the video is made. Not
    # declaring them here did not mean "use the default" — it meant the choice
    # was read off the screen, sent, and dropped on the floor.
    review_script: bool = Form(True),
    render_speed: str = Form("balanced"),
    shot_pace: str = Form("steady"),
    brand_logo: bool = Form(False),
    action: str = Form(""),
    animate_actors: bool = Form(False),
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """Storyboard a voice-over the user already has, instead of generating one.

    The script gate works here too, and means something different: the words are
    already spoken, so reading them changes the subtitles rather than the voice.
    That is worth stopping for — a transcription mishears a name, and the burnt-in
    caption is the one place that mistake becomes permanent. Nothing is
    re-recorded, because `needs_voice` is ignored for an uploaded narration.
    """
    if not config.has_key("openai"):
        raise HTTPException(
            status_code=400,
            detail="Uploaded voice-overs need an OpenAI key — the subtitles are timed "
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
        "review_script": review_script, "render_speed": render_speed,
        "shot_pace": shot_pace, "brand_logo": brand_logo,
        "action": action or None, "animate_actors": animate_actors,
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


def _staged_uploads(request: dict[str, Any]) -> list[Path]:
    """Files the user handed us for this project, staged outside its folder.

    A voice-over, or the video being dubbed or subtitled, is written to
    `uploads/` before there is a project folder to put it in — so deleting the
    folder never touched it, and the largest file in the whole app quietly
    outlived the project it belonged to.

    Only paths inside `uploads/` are returned. A request is stored JSON, and JSON
    is the kind of thing that ends up holding `/etc/passwd` if nobody checks.
    """
    root = (config.DATA_DIR / "uploads").resolve()
    out: list[Path] = []
    for field in ("narration_audio", "source_video"):
        raw = request.get(field)
        if not raw:
            continue
        try:
            path = Path(str(raw)).resolve()
            if path.is_relative_to(root) and path.is_file():
                out.append(path)
        except (OSError, ValueError):
            continue
    return out


def _tree_bytes(path: Path) -> int:
    """How much disk a folder is actually using, missing folders counting as none."""
    total = 0
    if not path.exists():
        return 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:  # noqa: PERF203 - a file that vanished mid-walk is zero
            continue
    return total


@app.get("/api/storage")
async def storage_used() -> dict[str, Any]:
    """What is taking up room, so clearing it is a decision rather than a guess."""
    jobs = store.list_jobs(limit=500)
    return {
        "jobs": len(jobs),
        # The three places a project's bytes end up. They are separate on
        # purpose: on a service with no volume the folder is empty after every
        # redeploy while the database copy is not, and "why is it still full"
        # has a different answer in each case.
        "project_bytes": _tree_bytes(config.PROJECTS_DIR),
        "media_bytes": store.media_bytes(),
        "db_bytes": config.DB_PATH.stat().st_size if config.DB_PATH.exists() else 0,
        # Voice-overs and videos handed to us for dubbing. They are staged
        # outside any project folder, which is exactly why they are worth
        # counting separately — they used to outlive the project they belonged to.
        "upload_bytes": _tree_bytes(config.DATA_DIR / "uploads"),
    }


@app.delete("/api/jobs")
async def delete_every_job(confirm: str = "") -> dict[str, Any]:
    """Delete every project — the rows, the folders, the kept media, the bucket.

    Deliberately the same path as deleting one, run over all of them, rather
    than a faster truncate: a project's bytes live in four places and only that
    path knows all four. Heroes, the brand kit, music and API keys are not
    projects and are not touched.
    """
    if confirm != "hammasi":
        raise HTTPException(
            status_code=400,
            detail="Tasdiqlanmadi — bu hamma loyihalarni butunlay o'chiradi.")
    gone, failed = 0, 0
    for job in store.list_jobs(limit=500):
        try:
            await delete_job(job["id"])
            gone += 1
        except Exception:  # noqa: BLE001 - one stuck project must not stop the rest
            failed += 1
    # Whatever the loop could not account for: a folder whose row had already
    # gone, an upload staged for a project that never started.
    if config.PROJECTS_DIR.exists():
        for leftover in config.PROJECTS_DIR.iterdir():
            if leftover.is_dir() and store.get_job(leftover.name) is None:
                shutil.rmtree(leftover, ignore_errors=True)
    # Every project is gone, so nothing can still be referring to a staged
    # upload. Safe to empty here in a way it never is for a single deletion.
    if not failed:
        shutil.rmtree(config.DATA_DIR / "uploads", ignore_errors=True)
    return {"deleted": gone, "failed": failed}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, Any]:
    """Delete a project — everywhere it exists, not only where it is cheapest.

    The row, the database copies, the local folder, the file you uploaded, the
    prompts it still had queued, and the bucket. Somebody deleting a video wants
    it gone, and a copy left at a public URL is the one that matters.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    uploads = _staged_uploads(job.get("request") or {})

    if not store.delete_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found.")
    shutil.rmtree(config.PROJECTS_DIR / job_id, ignore_errors=True)
    store.drop_image_tasks(job_id)
    for path in uploads:
        path.unlink(missing_ok=True)
    remote = await storage.remove_folder(job_id)
    return {"deleted": True, "uploads_removed": len(uploads), "remote_removed": remote}


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

    dropped = 0
    if patch.image_provider is not None:
        wanted = patch.image_provider.strip().lower()
        if wanted and wanted not in config.IMAGE_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"'{wanted}' — bunday rasm provayderi yo'q.")
        if wanted and not config.image_provider_ready(wanted):
            raise HTTPException(
                status_code=400,
                detail=f"'{wanted}' sozlanmagan — kalit yoki manzil qo'shing.")
        was = (request.get("image_provider") or config.IMAGE_PROVIDER).lower()
        request["image_provider"] = wanted or None
        now = wanted or config.IMAGE_PROVIDER
        if was == "flow" and now != "flow":
            # The prompts this project parked in the Flow queue will never be
            # answered now — nothing is going to come and draw them, and the
            # render would sit out the full waiting period on every one before
            # giving up. Clearing them is what "changed my mind" means here.
            dropped = store.drop_image_tasks(job_id)
        if was != now:
            store.update_job(
                job_id, log=f"Rasm provayderi: {was} → {now}"
                            + (f" ({dropped} ta navbatdagi prompt bekor qilindi)"
                               if dropped else ""))

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


@app.post("/api/jobs/{job_id}/scenes/{index}/layers/{layer_id}/everywhere")
async def layer_everywhere(job_id: str, index: int, layer_id: str) -> dict[str, Any]:
    """Put this layer on every scene, for the whole of each one.

    What a channel mark is: the same thing in the same corner from the first
    frame to the last. Doing it by hand is adding one sticker forty times and
    lining it up forty times, and it is also how you cover a watermark somebody
    else's generator burned into the picture.

    Copied rather than shared: once it is there it behaves like any other layer,
    so a single scene where it sits over somebody's face can be nudged without
    the other thirty-nine moving with it.
    """
    _job, scenes = _editable_job(job_id)
    source = next((s for s in scenes if s["index"] == index), None)
    if source is None:
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    layer = next((l for l in (source.get("overlays") or [])
                  if l.get("id") == layer_id), None)
    if layer is None:
        raise HTTPException(status_code=404, detail="Bu sahnada bunday qatlam yo'q.")

    stamped = 0
    for scene in scenes:
        if scene["index"] == index:
            # Spanning the whole scene is the point, so the source is fixed too.
            layer["start"], layer["end"] = 0, 0
            continue
        # Its own id per scene: two layers sharing one would make "delete this
        # one" ambiguous, and the editor addresses layers by id.
        copy = {**layer, "id": f"{layer_id}-{scene['index']}", "start": 0, "end": 0}
        rest = [l for l in (scene.get("overlays") or [])
                if l.get("id") != copy["id"]]
        scene["overlays"] = ov.normalize_all([*rest, copy])
        stamped += 1

    source["overlays"] = ov.normalize_all(source.get("overlays") or [])
    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Belgi {stamped + 1} ta sahnaga qo'yildi")
    return {"id": job_id, "scenes": len(scenes), "stamped": stamped + 1}


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

    if patch.speaker is not None:
        chosen = patch.speaker.strip()
        if chosen and not store.get_heroes([chosen]):
            raise HTTPException(status_code=400, detail="Bunday qahramon yo'q.")
        if chosen != (scene.get("speaker") or ""):
            scene["speaker"] = chosen
            # A different voice means a different recording, and the timings
            # that follow from it — the picture is untouched.
            scene["needs_voice"] = True

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
    if not (body.image or body.voice or body.language):
        raise HTTPException(status_code=400, detail="Nothing to regenerate.")

    redo_voice = body.voice and not job["request"].get("narration_audio")

    # Changing the language is a re-record of the whole video, whatever else was
    # asked for: the words all change, so every one of them has to be read again.
    switching = (body.language or "").strip()
    if switching:
        if switching not in config.LANGUAGES:
            raise HTTPException(status_code=400,
                                detail=f"'{switching}' tili qo'llab-quvvatlanmaydi.")
        if job["request"].get("narration_audio"):
            raise HTTPException(
                status_code=400,
                detail="Bu videoda o'z audiongiz ishlatilgan — tilini o'zgartirib bo'lmaydi.")
        if not config.llm_ready():
            raise HTTPException(status_code=400,
                                detail="Tarjima uchun AI kaliti kerak.")
        if switching == job["request"].get("language"):
            switching = ""

    # Re-recording is exactly when you notice the voice was wrong, so it can be
    # changed here. It applies to the whole video — a narrator who changes
    # halfway through is a defect — and the other scenes are marked rather than
    # re-recorded now, so one scene's fix does not quietly become a whole
    # re-record you did not ask for.
    # The range is worked out first, because what is about to be recorded decides
    # what has to be marked as stale. Checked here too, where a bad range can
    # still be a 400 rather than a job that runs and records nothing.
    span = None
    if body.from_index is not None or body.to_index is not None:
        known = sorted(s["index"] for s in scenes)
        low = body.from_index if body.from_index is not None else known[0]
        high = body.to_index if body.to_index is not None else known[-1]
        if low > high:
            raise HTTPException(status_code=400,
                                detail="Boshlanish raqami tugashidan katta bo'lmasin.")
        if not any(low <= i <= high for i in known):
            raise HTTPException(status_code=400,
                                detail=f"{low + 1}–{high + 1} oralig'ida sahna yo'q.")
        span = (low, high)

    if span is not None:
        about_to = {i for i in (s["index"] for s in scenes) if span[0] <= i <= span[1]}
    elif body.all_scenes:
        about_to = {s["index"] for s in scenes}
    else:
        about_to = {index}

    # Re-recording is exactly when you notice the voice was wrong, so it can be
    # changed here. It applies to the whole video — a narrator who changes
    # halfway through is a defect — and the scenes that are not about to be
    # recorded are marked rather than recorded now, so one fix does not quietly
    # become a whole re-record you did not ask for.
    #
    # Marked against what is *about to be recorded*, not against the scene that
    # happened to be open: with a range, the open scene is usually outside it,
    # and skipping it left one scene silently keeping the old voice.
    request = dict(job["request"])
    if _apply_voice(job_id, request, body.tts_provider, body.voice_id):
        store.replace_request(job_id, request)
        for scene in scenes:
            if scene["index"] not in about_to:
                scene["needs_voice"] = True
        store.update_job(job_id, result={"scenes": scenes},
                         log="Voice changed — other scenes will follow on render")
        redo_voice = redo_voice or bool(body.voice)

    store.update_job(job_id, status="running", step="regenerate", progress=10)
    _launch(lambda: pipeline.regenerate_scene(
        job_id, index, redo_image=body.image,
        redo_voice=redo_voice or bool(switching),
        redo_all_voices=body.all_scenes or bool(switching),
        voice_range=None if switching else span,
        language=switching or None, note=body.note.strip()), job_id)
    return {"id": job_id, "status": "running",
            "voice_range": None if switching else span,
            "language": switching or job["request"].get("language", "")}


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


@app.post("/api/jobs/{job_id}/images/redo", status_code=202)
async def redo_placeholder_images(job_id: str) -> dict[str, Any]:
    """Draw again every scene that ended up with a grey rectangle.

    A placeholder is a file, so nothing downstream treats it as missing — the
    render uses it and the counters call it done. Getting rid of them meant
    regenerating scenes one at a time, eighteen times, which is not a thing
    anybody does.
    """
    job, scenes = _editable_job(job_id)
    wanted = pipeline.placeholder_scenes(scenes)
    if not wanted:
        raise HTTPException(status_code=400,
                            detail="Bu loyihada qayta yasaydigan rasm yo'q.")
    store.update_job(job_id, status="running", step="images", progress=55,
                     log=f"{len(wanted)} ta rasm qayta yasalmoqda")
    _launch(lambda: pipeline.redo_placeholders(job_id), job_id)
    return {"id": job_id, "status": "running", "scenes": wanted}


@app.delete("/api/jobs/{job_id}/logs")
async def clear_job_logs(job_id: str) -> dict[str, Any]:
    """Empty a project's journal and clear the warnings stuck to it.

    Warnings are sticky on purpose — a picture that came out wrong should not
    stop saying so. The cost is that a project moved between providers carries
    every complaint the old one ever made, and those are not news about the new
    one. Nothing that was made is touched.
    """
    if store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    store.clear_logs(job_id)
    return {"id": job_id, "cleared": True}


@app.get("/api/jobs/{job_id}/prompts")
async def scene_prompts(job_id: str) -> dict[str, Any]:
    """The picture prompts as a flat numbered list, ready to be copied one by one.

    What "men o'zim yasayman" needs to be usable: the prompts are already
    written, but reading a hundred of them out of the scene panel one tap at a
    time is not work anybody finishes.
    """
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    return pipeline.prompt_sheet(job)


@app.get("/api/jobs/{job_id}/prompts.txt")
async def scene_prompts_text(job_id: str) -> Response:
    """The same sheet as a file — a phone with no clipboard manager still has files."""
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found.")
    sheet = pipeline.prompt_sheet(job)
    return Response(
        content=pipeline.prompt_sheet_text(sheet).encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition":
                 f'attachment; filename="{job_id}-promptlar.txt"'},
    )


@app.post("/api/jobs/{job_id}/images", status_code=202)
async def upload_scene_images(
    job_id: str,
    images: list[UploadFile] = File(...),
    mode: str = Form("auto"),
    scope: str = Form("empty"),
) -> dict[str, Any]:
    """Hand over a folder of pictures and let the studio sort them into scenes.

    The endpoint behind making the images yourself: the prompts leave in scene
    order and the files come back as a heap named after whatever made them, so
    the ordering has to be recovered rather than assumed. `auto` recovers it by
    looking at the pictures; `order` trusts the order they were sent in.

    Answers immediately, because looking at a hundred pictures is not something
    to hold a browser open for — the job's progress carries the rest.
    """
    _editable_job(job_id)
    if mode not in {"auto", "order"}:
        raise HTTPException(status_code=400, detail="mode: 'auto' yoki 'order'.")
    if len(images) > pipeline.MAX_UPLOAD_IMAGES:
        raise HTTPException(
            status_code=400,
            detail=f"Bir martada {pipeline.MAX_UPLOAD_IMAGES} tagacha rasm yuklash mumkin "
                   f"— siz {len(images)} ta yubordingiz.")
    if mode == "auto" and not config.llm_ready():
        raise HTTPException(
            status_code=400,
            detail="Rasmlarni o'zi taqsimlashi uchun matn modeli kaliti kerak. "
                   "Kalit qo'shing yoki 'fayl tartibida' usulini tanlang.")

    inbox = pipeline.inbox_for(job_id)
    staged: list[Path] = []
    for position, upload in enumerate(images):
        data, _mime, ext = await _read_upload(upload, IMAGE_TYPES)
        # Numbered on the way in, because file order is what `order` mode means
        # and what `auto` falls back to — and a browser does not promise to keep
        # the order once the files are on disk. The name given by whatever made
        # the picture rides along: it is often the only clue the model gets about
        # a still that could belong to either of two similar scenes.
        given = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(upload.filename or "").stem)[:60]
        path = inbox / f"{position:04d}-{given or 'image'}{ext}"
        path.write_bytes(data)
        staged.append(path)

    store.update_job(job_id, status="running", step="arrange", progress=30,
                     log=f"{len(staged)} ta rasm qabul qilindi")
    _launch(lambda: pipeline.arrange_uploaded_images(
        job_id, staged, mode=mode, scope=scope), job_id)
    return {"id": job_id, "status": "running", "received": len(staged), "mode": mode}


@app.post("/api/jobs/{job_id}/scenes/{index}/voice")
async def upload_scene_voice(
    job_id: str, index: int, audio: UploadFile = File(...)
) -> dict[str, Any]:
    """Say a scene's line yourself, in place of the synthesized reading."""
    _editable_job(job_id)
    data, _mime, ext = await _read_upload(audio, AUDIO_TYPES)
    try:
        scene = await pipeline.replace_scene_voice(job_id, index, data, ext)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except video.RenderError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Bu audio o'qilmadi — boshqa formatda urinib ko'ring ({exc})") from exc
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {index} does not exist.")
    return pipeline.public_scene(job_id, scene)


@app.post("/api/jobs/{job_id}/render", status_code=202)
async def render_job(job_id: str) -> dict[str, Any]:
    """Finish a reviewed draft, or re-render a finished video after edits.

    A render that has to wait its turn says so. It used to claim to be rendering
    from the moment the button was pressed, so a second project queued behind the
    first looked like one that had hung — and the honest answer, "you are next",
    was never on screen.
    """
    _editable_job(job_id)
    busy = len(_queue) >= config.MAX_CONCURRENT_JOBS
    if busy:
        store.update_job(job_id, status="queued", step="navbat", progress=74,
                         log=f"Navbatda {len(_queue) - config.MAX_CONCURRENT_JOBS + 1}-chi "
                             "— o'z navbatida o'zi boshlanadi")
    else:
        store.update_job(job_id, status="rendering", step="render", progress=74)
    _launch(
        lambda: pipeline.run_render(job_id), job_id,
        on_start=(lambda: store.update_job(job_id, status="rendering", step="render",
                                           log="Navbat keldi — render boshlandi"))
        if busy else None,
    )
    return {"id": job_id, "status": "queued" if busy else "rendering",
            "queue_place": queue_place(job_id)}


@app.post("/api/jobs/{job_id}/resume", status_code=202)
async def resume_job(job_id: str) -> dict[str, Any]:
    """Carry on from where a run stopped, paying only for what is missing."""
    job = _get_job_or_404(job_id)
    if job["status"] in _BUSY:
        raise HTTPException(status_code=409, detail="Bu loyiha allaqachon ishlayapti.")

    store.update_job(job_id, status="running", step="resume", error="",
                     log="Davom ettirilmoqda")
    _launch(lambda: pipeline.resume_job(job_id), job_id)
    return {"id": job_id, "status": "running"}


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

    # Anything this job had queued for somebody else to draw is dropped with it.
    # Without this a cancelled render leaves prompts in the Flow queue that
    # nobody is waiting for, and the extension dutifully makes pictures for a
    # video that no longer exists.
    dropped = store.drop_image_tasks(job_id)

    scenes = (job.get("result") or {}).get("scenes") or []
    store.update_job(
        job_id,
        status="review" if scenes else "failed",
        step="review" if scenes else "failed",
        progress=72 if scenes else job.get("progress", 0),
        # Scenes are only written between stages, so a cancel during the first
        # pass has nothing to go back to; one that lands later keeps the draft.
        error="" if scenes else "Stopped before it finished.",
        log=("Stopped by you."
             + (f" {dropped} ta Flow so'rovi ham bekor qilindi." if dropped else "")),
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
    if not (config.has_key("gemini") or config.has_key("openai")):
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


# ── pictures made somewhere else ──────────────────────────────────────────────
#
# The `flow` image provider does not call anything. It parks each scene's prompt
# in a queue and waits, and these are the two ends of that queue: something takes
# a prompt, and something hands back a picture. What that something is — the
# browser extension in `extension/`, or a person with a file picker — is not this
# app's business, which is the point. The pictures are made in a browser that is
# already signed in to Google; no account, no cookie and no token for it ever
# reaches the server.

def _task_out(task: dict[str, Any]) -> dict[str, Any]:
    job = store.get_job(task.get("job_id", "")) or {}
    return {
        "id": task.get("id", ""),
        "job_id": task.get("job_id", ""),
        "title": (job.get("result") or {}).get("title")
                 or (job.get("request") or {}).get("topic", ""),
        "scene": task.get("scene", 0),
        "prompt": task.get("prompt", ""),
        "aspect": task.get("aspect", "16:9"),
        "status": task.get("status", ""),
        "taken_by": task.get("taken_by", ""),
        "error": task.get("error", ""),
        "created_at": task.get("created_at", ""),
    }


@app.get("/api/flow/tasks")
async def flow_tasks(job_id: str = "", pending: bool = True) -> dict[str, Any]:
    """What is still waiting for a picture. Read-only, and safe to poll."""
    rows = store.list_image_tasks(job_id=job_id, pending_only=pending)
    return {"tasks": [_task_out(t) for t in rows], "waiting": len(rows),
            "on": config.IMAGE_PROVIDER == "flow",
            # What it goes back to when switched off, so the button can say it.
            "off_provider": config.IMAGE_PROVIDER_ENV}


@app.get("/api/flow-agent")
async def flow_agent_status() -> dict[str, Any]:
    """Whether the ready-made extension can be handed out, and where it points."""
    return {**flowext.status(), "url": config.FLOW_AGENT_URL}


def _said(resp: "httpx.Response") -> str:
    """The reason a service gave, as a sentence rather than as JSON."""
    try:
        body = resp.json()
        if isinstance(body, dict):
            return str(body.get("detail") or body.get("error") or "")[:300]
    except Exception:  # noqa: BLE001 - a plain-text answer is still an answer
        pass
    return resp.text[:300]


class FlowProject(BaseModel):
    """Which Google Flow project the pictures should be filed into."""

    project_id: str = Field(min_length=8, max_length=80)
    # Bring the browser to it as well. Google decides which project a picture
    # belongs to partly from what the tab is showing, so a backend pointed at one
    # project while the tab shows another files pictures nobody asked for.
    open_tab: bool = True


@app.get("/api/flow-agent/project")
async def flow_project() -> dict[str, Any]:
    """Which Flow project the agent is drawing into, as it reports it."""
    if not config.FLOW_AGENT_URL:
        return {"ready": False, "why": "Flow Agent manzili sozlanmagan."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            got = await client.get(f"{config.FLOW_AGENT_URL}/sarideo/project")
        if got.status_code == 404:
            return {"ready": False, "why": "Flow Agent eski — uni qayta deploy qiling."}
        got.raise_for_status()
        return {"ready": True, **got.json()}
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return {"ready": False, "why": f"Flow Agent javob bermadi: {exc}"}


@app.post("/api/flow-agent/project")
async def set_flow_project(body: FlowProject) -> dict[str, Any]:
    """Point the agent at another Flow project, and open it in the browser.

    A project fills up. Changing it used to mean editing an environment variable
    on the hosting platform and waiting for a redeploy, which is a poor answer to
    "this one is full" and an impossible one from a phone. The agent takes it at
    runtime instead, and tells whichever browser is helping to open it.
    """
    if not config.FLOW_AGENT_URL:
        raise HTTPException(status_code=400, detail="Flow Agent manzili sozlanmagan.")
    headers = ({"Authorization": f"Bearer {config.FLOW_AGENT_KEY}"}
               if config.FLOW_AGENT_KEY else {})
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            sent = await client.post(
                f"{config.FLOW_AGENT_URL}/sarideo/project",
                json={"project_id": body.project_id.strip(), "open_tab": body.open_tab},
                headers=headers)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"Flow Agent javob bermadi: {exc}") from exc
    if sent.status_code == 404:
        raise HTTPException(
            status_code=400,
            detail="Flow Agent bu funksiyani bilmaydi — uni qayta deploy qiling.")
    if sent.status_code >= 400:
        raise HTTPException(status_code=sent.status_code,
                            detail=_said(sent) or "Flow Agent rad etdi.")
    # Kept here too, so the field shows the right thing after a restart and
    # anybody reading the settings can see which project is in use.
    store.set_setting(FLOW_PROJECT_KEY, {"project_id": body.project_id.strip()})
    return sent.json()


@app.get("/api/flow-agent/extension.zip")
async def flow_agent_extension() -> Response:
    """Flow Agent's extension, already pointed at this app's backend.

    The address is compiled into the extension's `config.js` and repeated in its
    manifest permissions, with no field in its panel to change either — so
    connecting it to a backend of your own otherwise means editing two files by
    hand on a laptop from written instructions. This is the same folder with
    those edits already made: download, unzip, load unpacked.
    """
    try:
        data = flowext.build()
    except flowext.NotReady as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition":
                 'attachment; filename="flow-agent-extension.zip"'},
    )


@app.post("/api/flow/mode")
async def flow_mode(body: FlowMode) -> dict[str, Any]:
    """Turn Flow on or off for every video from now on.

    A per-video dropdown was the only way to choose this, which made it a thing
    you had to remember every time rather than a mode the app is in. Stored, so
    it survives a restart.
    """
    chosen = config.set_image_provider("flow" if body.on else config.IMAGE_PROVIDER_ENV)
    store.set_setting(IMAGE_PROVIDER_KEY, chosen)
    return await flow_tasks()


@app.post("/api/flow/next")
async def flow_next(worker: str = "") -> dict[str, Any]:
    """Claim the oldest prompt nobody is working on.

    A claim is soft: it expires, so a browser tab closed halfway through hands
    the scene back instead of holding the render until it gives up.
    """
    task = store.claim_image_task(worker.strip()[:60] or "anon")
    return {"task": _task_out(task) if task else None}


@app.post("/api/flow/tasks/{task_id}/image")
async def flow_deliver(task_id: str, image: UploadFile = File(...)) -> dict[str, Any]:
    """Here is the picture. Write it where the render is waiting for it."""
    task = store.get_image_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Bunday so'rov yo'q.")
    if task["status"] == "done":
        raise HTTPException(status_code=409, detail="Bu so'rovga rasm allaqachon berilgan.")

    data, _mime, _ext = await _read_upload(image, IMAGE_TYPES)
    target = Path(task["out_path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Normalised here rather than at the far end, because the far end is a
        # browser download and could be anything the model felt like producing.
        # RGB PNG is what ffmpeg will not trip over.
        with PILImage.open(io.BytesIO(data)) as img:
            img.convert("RGB").save(target, format="PNG")
    except Exception as exc:  # noqa: BLE001 - a bad file is the caller's problem
        raise HTTPException(status_code=400,
                            detail=f"Rasmni o'qib bo'lmadi: {exc}") from exc

    store.finish_image_task(task_id)
    return {"ok": True, "task": _task_out(store.get_image_task(task_id) or {})}


@app.patch("/api/flow/tasks/{task_id}")
async def flow_rewrite(task_id: str, body: FlowPrompt) -> dict[str, Any]:
    """Change the prompt before anything is drawn from it.

    The prompts are written by a model, and a model writing a hundred of them
    gets some of them wrong. When the pictures are being made by hand — or by a
    browser you are watching — the moment to fix that is while the prompt is
    still sitting in the queue, not after the picture arrives.
    """
    task = store.get_image_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Bunday so'rov yo'q.")
    if task["status"] == "done":
        raise HTTPException(status_code=409,
                            detail="Bu so'rov bajarilgan — tahrirlashning ma'nosi yo'q.")
    if not store.rewrite_image_task(task_id, body.prompt.strip()):
        raise HTTPException(status_code=409, detail="Tahrirlab bo'lmadi.")
    return {"ok": True, "task": _task_out(store.get_image_task(task_id) or {})}


@app.post("/api/flow/tasks/{task_id}/fail")
async def flow_fail(task_id: str, body: FlowFail) -> dict[str, Any]:
    """It did not work. Say so, rather than letting the render wait it out."""
    if not store.get_image_task(task_id):
        raise HTTPException(status_code=404, detail="Bunday so'rov yo'q.")
    if body.retry:
        store.release_image_task(task_id)
    else:
        store.finish_image_task(task_id, error=body.reason or "Flow'da chiqmadi")
    return {"ok": True, "task": _task_out(store.get_image_task(task_id) or {})}


@app.post("/api/videos/subtitle", status_code=202)
async def subtitle_video(
    video_file: UploadFile = File(..., alias="video"),
    language: str = Form(""),
    subtitle_style: str = Form("bold"),
    burn_subtitles: bool = Form(True),
    render_speed: str = Form("balanced"),
    topic: str = Form(""),
) -> dict[str, Any]:
    """Put subtitles on a video the user already has.

    `language` may be empty, which means "work it out from the audio" — the
    transcribers detect it, and asking someone to name the language of a video
    they are holding is asking them to do the machine's job.
    """
    if language and language not in config.LANGUAGES:
        raise HTTPException(status_code=400, detail=f"Noma'lum til '{language}'.")
    if not (config.has_key("openai") or config.has_key("gemini")):
        raise HTTPException(
            status_code=400,
            detail="Videoni tinglash uchun OpenAI yoki Gemini kaliti kerak — "
                   "kutubxonadagi «API kalitlari» ga qo'shing.")
    if not video.ffmpeg_available():
        raise HTTPException(status_code=500, detail="Bu konteynerda ffmpeg yo'q.")

    data, _mime, ext = await _read_upload(video_file, VIDEO_TYPES)
    staged = config.DATA_DIR / "uploads" / f"{store.new_id('sub')}{ext}"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(data)

    job_id = store.create_job({
        "kind": "subtitle",
        "topic": topic.strip() or Path(video_file.filename or "video").stem,
        "source_video": str(staged),
        "language": language,
        "subtitle_style": subtitle_style,
        "caption_style": subs.resolve_style(subtitle_style),
        "burn_subtitles": burn_subtitles,
        "render_speed": render_speed,
        "video_format": "16:9",
        "auto_render": True,
    })
    _launch(lambda: pipeline.run_subtitle(job_id), job_id)
    return {"id": job_id, "status": "queued"}


# ── the script, before anything is made from it ───────────────────────────────

def _script_stage(job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A job waiting at the script gate. Anything else is refused by name."""
    job = _get_job_or_404(job_id)
    if job["status"] != "script":
        raise HTTPException(
            status_code=409,
            detail="Bu loyiha matnni ko'rish bosqichida emas "
                   f"(hozir: {job['status']}).")
    scenes = (job.get("result") or {}).get("scenes") or []
    if not scenes:
        raise HTTPException(status_code=400, detail="Bu loyihada matn yo'q.")
    return job, scenes


@app.post("/api/jobs/{job_id}/script/revise")
async def revise_script(job_id: str, body: ScriptNote) -> dict[str, Any]:
    """Rewrite the script to a note, before a picture or a recording exists.

    Synchronous on purpose. This is a conversation with the text in front of you
    — you say what is wrong, you read what came back, you say the next thing —
    and a progress bar between the two would break the loop it exists to serve.
    """
    job, scenes = _script_stage(job_id)
    if not config.llm_ready():
        raise HTTPException(status_code=400, detail="Tuzatish uchun AI kaliti kerak.")

    request = job.get("request") or {}
    before = [str(s.get("narration") or "") for s in scenes]
    try:
        changed, said = await skills.revise_script(
            scenes=scenes, note=body.note,
            language=request.get("language", "en"),
            tone=request.get("tone", ""),
            title=(job.get("result") or {}).get("title") or request.get("topic", ""),
        )
    except Exception as exc:  # noqa: BLE001 - the model's failure, said plainly
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc

    store.update_job(job_id, result={"scenes": scenes},
                     log=f"Matn tuzatildi — {changed} ta sahna o'zgardi: {body.note[:80]}")
    return {
        "id": job_id,
        "changed": changed,
        "note_back": said,
        # Which ones moved, so the page can mark them rather than make you read
        # the whole thing again looking for the difference.
        "changed_indexes": [s["index"] for s, was in zip(scenes, before)
                            if str(s.get("narration") or "") != was],
        "scenes": [pipeline.public_scene(job_id, s) for s in scenes],
    }


@app.post("/api/jobs/{job_id}/script/approve", status_code=202)
async def approve_script(job_id: str) -> dict[str, Any]:
    """Agree the script, and let the expensive half begin."""
    _script_stage(job_id)
    store.update_job(job_id, status="queued", step="queued", progress=20,
                     log="Matn tasdiqlandi — ovoz va rasmlar boshlanmoqda")
    _launch(lambda: pipeline.continue_after_script(job_id), job_id)
    return {"id": job_id, "status": "queued", "place": queue_place(job_id)}


@app.post("/api/jobs/{job_id}/shorts/suggest")
async def suggest_shorts(job_id: str, count: int = 3) -> dict[str, Any]:
    """Which parts of this long video would stand alone as Shorts.

    Suggesting costs a model call, so it is asked for rather than run on every
    finished video — and the answer carries the real length of each cut, taken
    from the recorded voice-over, so nothing here is a guess you pay for later.
    """
    job = _get_job_or_404(job_id)
    if not config.llm_ready():
        raise HTTPException(status_code=400,
                            detail="Tavsiya uchun AI kaliti kerak.")
    scenes = (job.get("result") or {}).get("scenes") or []
    if len(scenes) < 2:
        raise HTTPException(status_code=400,
                            detail="Bu video bo'linish uchun juda qisqa.")
    try:
        found = await pipeline.shorts_for(job_id, count=max(1, min(count, 6)))
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the model's failure, said plainly
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc
    return {"shorts": found, "max_seconds": pipeline.SHORT_MAX_SECONDS}


@app.post("/api/jobs/{job_id}/shorts/all", status_code=201)
async def make_every_short(job_id: str, body: ShortsAll) -> dict[str, Any]:
    """Find every Short in this video and cut all of them.

    However many there are — five or ten makes no difference to the asking, and
    the model is told to stop when the video stops holding stretches that stand
    alone rather than to reach a number. Cutting is cheap: the pictures and the
    voice already exist, so what this spends is render time, and the renders
    queue rather than running at once.
    """
    job = _get_job_or_404(job_id)
    if not config.llm_ready():
        raise HTTPException(status_code=400, detail="Tavsiya uchun AI kaliti kerak.")
    scenes = (job.get("result") or {}).get("scenes") or []
    if len(scenes) < 2:
        raise HTTPException(status_code=400, detail="Bu video bo'linish uchun juda qisqa.")

    try:
        found = await pipeline.shorts_for(job_id, count=body.limit)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the model's failure, said plainly
        raise HTTPException(status_code=502, detail=str(exc)[:300]) from exc

    if not found:
        raise HTTPException(
            status_code=400,
            detail="Bu videodan alohida ishlaydigan bo'lak topilmadi — "
                   "o'zingiz tanlab kesib ko'ring.")

    made: list[dict[str, Any]] = []
    failed: list[str] = []
    for pick in found:
        try:
            short_id = await pipeline.cut_short(
                job_id, pick["from_index"], pick["to_index"], title=pick.get("title", ""),
                video_format=body.video_format, regenerate_images=body.regenerate_images)
        except pipeline.PipelineError as exc:
            # One bad range must not cost you the other four.
            failed.append(f"{pick['from_index'] + 1}–{pick['to_index'] + 1}: {exc}")
            continue
        if short_id is None:
            continue
        made.append({**pick, "id": short_id})
        if body.render:
            _launch(lambda sid=short_id: pipeline.run_render(sid, may_rebuild=True), short_id)

    if not made:
        raise HTTPException(status_code=400,
                            detail="; ".join(failed) or "Hech narsa kesilmadi.")
    return {"shorts": made, "count": len(made), "skipped": failed}


@app.post("/api/jobs/{job_id}/shorts", status_code=201)
async def make_short(job_id: str, body: ShortCut) -> dict[str, Any]:
    """Cut one run of scenes into a Short of its own, and start rendering it."""
    _get_job_or_404(job_id)
    try:
        short_id = await pipeline.cut_short(
            job_id, body.from_index, body.to_index, title=body.title,
            video_format=body.video_format, regenerate_images=body.regenerate_images)
    except pipeline.PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if short_id is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if body.render:
        _launch(lambda: pipeline.run_render(short_id, may_rebuild=True), short_id)
        return {"id": short_id, "status": "queued", "place": queue_place(short_id)}
    return {"id": short_id, "status": "review"}


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

SUBTITLE_KINDS = {
    "srt": ("application/x-subrip", "srt"),
    "vtt": ("text/vtt", "vtt"),
    "txt": ("text/plain; charset=utf-8", "txt"),
}


async def _captions_for(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Every cue in this video, whatever it was rendered by.

    Videos rendered from now on keep their cues with the job. Older ones only
    left an SRT on disk, so that file is read back and parsed — a subtitle you
    could download last week must not become undownloadable because the app
    learned a second format.
    """
    stored = (job.get("result") or {}).get("captions")
    if stored:
        return list(stored)

    path = config.PROJECTS_DIR / job["id"] / "subtitles.srt"
    if not path.exists():
        await _bring_back(f"{job['id']}/subtitles.srt", path)
    if path.exists():
        return subs.parse_srt(path.read_text(encoding="utf-8", errors="replace"))
    return []


@app.get("/api/jobs/{job_id}/subtitles.{kind}")
async def subtitles(job_id: str, kind: str) -> Response:
    """The whole video's subtitles, in the shape you are about to use them in.

    `srt` for an editor or a YouTube upload, `vtt` for a web player, `txt` for
    the description box — the same cues, so they cannot drift apart.
    """
    kind = (kind or "srt").lower()
    if kind not in SUBTITLE_KINDS:
        raise HTTPException(status_code=404,
                            detail=f"Subtitr formati '{kind}' yo'q — srt, vtt yoki txt.")
    job = _get_job_or_404(job_id)
    captions = await _captions_for(job)
    result = job.get("result") or {}
    # A video the app wrote has scenes; a video it was handed has blocks of
    # transcript. Either one is what the plain-text transcript is paragraphed by.
    scenes = result.get("scenes") or result.get("blocks") or []
    if not captions and not scenes:
        raise HTTPException(status_code=404,
                            detail="Bu videoda hali subtitr yo'q — avval render qiling.")
    # A timed format with no timings is an empty file, and an empty file that
    # downloads with a 200 looks like a working feature until you open it. The
    # words exist before the timings do — that is what the script stage *is* —
    # so the text is offered and the other two say why they cannot be.
    if not captions and kind in ("srt", "vtt"):
        raise HTTPException(
            status_code=409,
            detail=f"Vaqtlar hali hisoblanmagan — .{kind} render qilingandan "
                   "keyin tayyor bo'ladi. Hozir matnni .txt sifatida olsangiz bo'ladi.")

    if kind == "srt":
        body = subs.build_srt(captions)
    elif kind == "vtt":
        body = subs.build_vtt(captions)
    else:
        body = subs.build_text(captions, scenes)

    media, ext = SUBTITLE_KINDS[kind]
    stem = pipeline._slug((job.get("result") or {}).get("title")
                 or (job.get("request") or {}).get("topic") or job_id)
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'},
    )


@app.get("/api/jobs/{job_id}/download")
async def download(job_id: str) -> FileResponse:
    _get_job_or_404(job_id)
    folder = config.PROJECTS_DIR / job_id
    videos = [v for v in folder.glob("*.mp4")
              if not v.name.startswith(("clip_", "fuse_"))] if folder.exists() else []
    if not videos:
        raise HTTPException(status_code=404, detail="This job has no rendered video yet.")
    newest = max(videos, key=lambda p: p.stat().st_mtime)
    return FileResponse(newest, media_type="video/mp4", filename=newest.name)


async def _bring_back(path: str, target: Path) -> bool:
    """Fetch one project file from object storage, or from the database."""
    if await storage.fetch(path, target):
        return True
    data = await asyncio.to_thread(store.get_media, path)
    if data is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return True


@app.get("/api/files/{path:path}")
async def project_file(path: str) -> FileResponse:
    target = _safe_child(config.PROJECTS_DIR, path)
    if not target.exists() or not target.is_file():
        # The database remembers a project across a redeploy, but the disk it
        # was rendered on is gone. Bring the file back from wherever it was
        # kept — and keep the copy, so the render that follows finds it there.
        if not await _bring_back(path, target):
            raise HTTPException(status_code=404, detail="File not found.")
    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(target, media_type=media_type)


@app.exception_handler(HTTPException)
async def http_error(_, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(Exception)
async def unhandled(_, exc: Exception) -> JSONResponse:
    """Turn a database that cannot be reached into an answer, not a stack trace.

    Deliberately no fallback to the local file. Serving an empty library from
    SQLite while the real one sits in Postgres would look exactly like the data
    being gone, and somebody would re-upload it — which is worse than being told
    plainly that the connection string is wrong.
    """
    if pgstore.enabled() and pgstore.is_connection_problem(exc):
        return JSONResponse(status_code=503, content={"error": pgstore.explain(exc)})
    raise exc
