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

    target = workdir_for(job_id) / "images" / f"scene_{index:03d}.png"
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


def _recompute_starts(scenes: list[dict]) -> float:
    cursor = 0.0
    for scene in scenes:
        scene["start"] = cursor
        cursor += float(scene.get("audio_duration") or 0.0)
    return cursor


def _save_scenes(job_id: str, scenes: list[dict], **extra: Any) -> None:
    store.update_job(job_id, result={"scenes": scenes, **extra})


def _load_scenes(job: dict) -> list[dict]:
    return job.get("result", {}).get("scenes") or []


def public_scene(job_id: str, scene: dict) -> dict:
    """The shape the browser sees — editable fields plus a preview image."""
    index = scene["index"]
    return {
        "index": index,
        "narration": scene.get("narration", ""),
        "image_prompt": scene.get("image_prompt", ""),
        "motion": scene.get("motion", "zoom_in"),
        "motion_strength": round(float(scene.get("motion_strength") or 1.0), 2),
        "transition": scene.get("transition") or "",
        "on_screen_text": scene.get("on_screen_text", ""),
        "hero_ids": scene.get("hero_ids", []),
        "overlays": scene.get("overlays") or [],
        "start": round(float(scene.get("start", 0.0)), 2),
        "duration": round(float(scene.get("audio_duration", 0.0)), 2),
        "image_url": (
            f"/api/files/{job_id}/images/scene_{index:03d}.png?v={scene.get('image_version', 0)}"
            if scene.get("image_path")
            else None
        ),
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
            out_path=audio_dir / f"scene_{scene['index']:03d}",
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
            out_path=image_dir / f"scene_{scene['index']:03d}.png",
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

        store.update_job(job_id, result={"title": script.get("title"),
                                         "scene_count": len(scenes)})

        # --- image prompts ---------------------------------------------------
        _progress(job_id, "prompts", 24, "Designing the look of each scene")
        prompt_pack = await skills.build_image_prompts(
            scenes=scenes, art_style=request.get("art_style", "cinematic photorealistic"),
            video_format=request.get("video_format", "16:9"), heroes=heroes,
            title=script.get("title", request["topic"]),
        )
        scenes = prompt_pack["scenes"]

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
        clips: list[Path] = []
        clip_durations: list[float] = []

        for scene in scenes:
            duration = scene["audio_duration"] + transition
            picture_layers = [
                {**layer, "path": asset_paths[layer["asset_id"]]}
                for layer in ov.image_layers(scene, float(scene["audio_duration"]))
                if layer.get("asset_id") in asset_paths
            ]
            clip = await video.make_scene_clip(
                image=Path(scene["image_path"]), motion=scene.get("motion", "zoom_in"),
                duration=duration, width=width, height=height,
                strength=float(scene.get("motion_strength") or 1.0),
                image_overlays=picture_layers,
                out_path=workdir / f"clip_{scene['index']:03d}.mp4",
            )
            clips.append(clip)
            clip_durations.append(duration)
            _progress(job_id, "clips", 78 + int(12 * len(clips) / max(len(scenes), 1)),
                      f"Animated scene {len(clips)}/{len(scenes)}", status="rendering")

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


def _fail(job_id: str, exc: Exception, warnings: list[str]) -> None:
    detail = str(exc) or exc.__class__.__name__
    store.update_job(
        job_id, status="failed", step="failed", error=detail, log=f"Failed: {detail}",
        result={"traceback": traceback.format_exc()[-4000:], "warnings": warnings},
    )


# Backwards-compatible alias: a full run is a draft that renders itself.
run_job = run_draft
