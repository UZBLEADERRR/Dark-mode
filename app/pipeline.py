"""The end-to-end render pipeline.

topic -> script -> image prompts -> voice -> images -> captions -> clips -> MP4

Every stage reports progress into the job row, so the browser can follow along by
polling a single endpoint. Long-running network work (images, voice) runs
concurrently with a small semaphore rather than serially — a 40-scene video is
otherwise dominated by round-trip latency.
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
from .render import subtitles as subs
from .render import video


class PipelineError(RuntimeError):
    pass


def _norm(word: str) -> str:
    return re.sub(r"[^\w']", "", unicodedata.normalize("NFKC", word).casefold(), flags=re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _progress(job_id: str, step: str, percent: int, message: str | None = None) -> None:
    store.update_job(job_id, status="running", step=step, progress=percent, log=message or step)


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


# --- stages ------------------------------------------------------------------

async def _voice_scenes(
    *,
    scenes: list[dict],
    workdir: Path,
    provider: str,
    voice_id: str | None,
    language: str,
    job_id: str,
) -> list[str]:
    """Synthesize each scene, then attach durations, timings and start offsets."""
    audio_dir = workdir / "audio"
    align_provider = config.resolve_align_provider(provider)
    warnings: list[str] = []
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
        scene["raw_audio_duration"] = raw_duration
        scene["audio_duration"] = raw_duration + video.SCENE_GAP
        scene["words"] = words
        async with lock:
            done += 1
            _progress(
                job_id,
                "voice",
                25 + int(20 * done / max(len(scenes), 1)),
                f"Voice-over {done}/{len(scenes)}",
            )

    await _gather_limited([one(scene) for scene in scenes], config.TTS_CONCURRENCY)

    cursor = 0.0
    for scene in scenes:
        scene["start"] = cursor
        cursor += scene["audio_duration"]
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

    # Pass 1: where each scene starts speaking.
    starts: list[float] = []
    previous = 0.0
    for i, slice_ in enumerate(slices):
        start = 0.0 if i == 0 else (float(slice_[0]["start"]) if slice_ else previous)
        start = max(start, previous)
        starts.append(start)
        previous = start

    # Pass 2: a scene holds the screen until the next one speaks.
    for i, scene in enumerate(scenes):
        start = starts[i]
        end = starts[i + 1] if i + 1 < len(scenes) else total
        scene["start"] = start
        scene["audio_duration"] = max(0.6, end - start)
        scene["words"] = [
            {
                "text": w["text"],
                "start": max(0.0, float(w["start"]) - start),
                "end": max(0.0, float(w["end"]) - start),
            }
            for w in slices[i]
        ]


async def _render_images(
    *,
    scenes: list[dict],
    workdir: Path,
    heroes: list[dict],
    provider: str,
    aspect: str,
    size: tuple[int, int],
    job_id: str,
) -> list[str]:
    image_dir = workdir / "images"
    hero_paths = {h["id"]: config.HEROES_DIR / h["filename"] for h in heroes}
    warnings: list[str] = []
    done = 0
    lock = asyncio.Lock()

    async def one(scene: dict) -> None:
        nonlocal done
        refs = [
            hero_paths[h]
            for h in scene.get("hero_ids", [])
            if h in hero_paths and hero_paths[h].exists()
        ]
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
        async with lock:
            done += 1
            if warning:
                warnings.append(f"Scene {scene['index'] + 1}: {warning}")
            _progress(
                job_id,
                "images",
                48 + int(24 * done / max(len(scenes), 1)),
                f"Scene image {done}/{len(scenes)}",
            )

    await _gather_limited([one(scene) for scene in scenes], config.IMAGE_CONCURRENCY)
    return warnings


# --- entry point -------------------------------------------------------------

async def run_job(job_id: str) -> None:
    job = store.get_job(job_id)
    if job is None:
        return
    request = job["request"]

    workdir = config.PROJECTS_DIR / job_id
    warnings: list[str] = []

    try:
        if not video.ffmpeg_available():
            raise PipelineError("ffmpeg is not installed in this container.")

        workdir.mkdir(parents=True, exist_ok=True)

        fmt = config.FORMATS.get(request.get("video_format", "16:9"), config.FORMATS["16:9"])
        width, height = fmt["width"], fmt["height"]
        language = request.get("language", "en")
        heroes = store.get_heroes(request.get("hero_ids") or [])

        image_provider = (request.get("image_provider") or config.IMAGE_PROVIDER).lower()
        if not config.image_provider_ready(image_provider):
            raise PipelineError(
                f"The '{image_provider}' image provider has no API key configured."
            )

        uploaded_audio = request.get("narration_audio")
        tts_provider = (request.get("tts_provider") or config.TTS_PROVIDER).lower()

        # --- 1. script ------------------------------------------------------
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
                topic=request["topic"],
                transcript=transcript["text"],
                duration=total_audio,
                language=language,
                video_format=request.get("video_format", "16:9"),
                heroes=heroes,
            )
            scenes = script["scenes"]
            _split_uploaded_audio(scenes, transcript["words"], total_audio)
            narration_path = workdir / f"narration{source_audio.suffix or '.mp3'}"
            shutil.copyfile(source_audio, narration_path)
        else:
            if not config.tts_provider_ready(tts_provider):
                raise PipelineError(
                    f"The '{tts_provider}' voice provider has no API key configured."
                )
            _progress(job_id, "script", 8, "Writing the script")
            script = await skills.direct_script(
                topic=request["topic"],
                target_seconds=int(request.get("target_seconds", 180)),
                language=language,
                tone=request.get("tone", "cinematic documentary"),
                video_format=request.get("video_format", "16:9"),
                heroes=heroes,
            )
            scenes = script["scenes"]
            _progress(job_id, "script", 20, f"{len(scenes)} scenes written")

        store.update_job(job_id, result={"title": script.get("title"), "scene_count": len(scenes)})

        # --- 2. image prompts ------------------------------------------------
        _progress(job_id, "prompts", 24, "Designing the look of each scene")
        prompt_pack = await skills.build_image_prompts(
            scenes=scenes,
            art_style=request.get("art_style", "cinematic photorealistic"),
            video_format=request.get("video_format", "16:9"),
            heroes=heroes,
            title=script.get("title", request["topic"]),
        )
        scenes = prompt_pack["scenes"]

        # --- 3. voice --------------------------------------------------------
        if not uploaded_audio:
            _progress(job_id, "voice", 26, "Recording the voice-over")
            warnings += await _voice_scenes(
                scenes=scenes,
                workdir=workdir,
                provider=tts_provider,
                voice_id=request.get("voice_id"),
                language=language,
                job_id=job_id,
            )
            narration_path = await video.concat_narration(
                audio_paths=[Path(s["audio_path"]) for s in scenes],
                out_path=workdir / "narration.wav",
            )
            total_audio = sum(s["audio_duration"] for s in scenes)

        # --- 4. images -------------------------------------------------------
        _progress(job_id, "images", 48, "Generating scene images")
        warnings += await _render_images(
            scenes=scenes,
            workdir=workdir,
            heroes=heroes,
            provider=image_provider,
            aspect=fmt["aspect"],
            size=(width, height),
            job_id=job_id,
        )

        # --- 5. captions -----------------------------------------------------
        _progress(job_id, "captions", 74, "Writing and timing the subtitles")
        captions = await skills.build_captions(
            scenes=scenes, language=language, width=width, height=height
        )
        title_cards = [
            {
                "text": s["on_screen_text"],
                "start": s["start"] + 0.25,
                "end": min(s["start"] + 3.0, s["start"] + s["audio_duration"]),
            }
            for s in scenes
            if s.get("on_screen_text")
        ]

        ass_path = workdir / "subtitles.ass"
        subs.write_ass(
            ass_path,
            subs.build_ass(
                captions=captions,
                width=width,
                height=height,
                font=config.SUBTITLE_FONT,
                style=request.get("subtitle_style", "bold"),
                title_cards=title_cards,
            ),
        )
        srt_path = workdir / "subtitles.srt"
        srt_path.write_text(subs.build_srt(captions), encoding="utf-8")

        # --- 6. scene clips --------------------------------------------------
        _progress(job_id, "clips", 78, "Animating the scenes")
        transition = min(
            config.TRANSITION_SECONDS,
            max(0.2, min(s["audio_duration"] for s in scenes) / 2),
        )
        clip_dir = workdir / "clips"
        clips: list[Path] = []
        clip_durations: list[float] = []

        for scene in scenes:
            duration = scene["audio_duration"] + transition
            clip = await video.make_scene_clip(
                image=Path(scene["image_path"]),
                motion=scene.get("motion", "zoom_in"),
                duration=duration,
                width=width,
                height=height,
                out_path=clip_dir / f"clip_{scene['index']:03d}.mp4",
            )
            clips.append(clip)
            clip_durations.append(duration)
            _progress(
                job_id,
                "clips",
                78 + int(12 * len(clips) / max(len(scenes), 1)),
                f"Animated scene {len(clips)}/{len(scenes)}",
            )

        # --- 7. assemble -----------------------------------------------------
        _progress(job_id, "render", 90, "Rendering the final video")
        music_path = None
        if request.get("music_id"):
            record = store.get_music(request["music_id"])
            if record:
                candidate = config.MUSIC_DIR / record["filename"]
                if candidate.exists():
                    music_path = workdir / f"music{candidate.suffix}"
                    shutil.copyfile(candidate, music_path)

        # ffmpeg runs with the project folder as its cwd so every path in the
        # filter graph is a bare filename — no escaping of ':' or '\' needed.
        staged_clips = []
        for clip in clips:
            staged = workdir / clip.name
            if clip.resolve() != staged.resolve():
                shutil.copyfile(clip, staged)
            staged_clips.append(staged)

        out_name = f"{_slug(script.get('title', request['topic']))}.mp4"
        out_path = workdir / out_name
        await video.assemble(
            clips=staged_clips,
            clip_durations=clip_durations,
            narration=narration_path,
            total_duration=total_audio,
            out_path=out_path,
            workdir=workdir,
            subtitle_file=ass_path if request.get("burn_subtitles", True) else None,
            music=music_path,
        )

        # --- 8. publish ------------------------------------------------------
        _progress(job_id, "publish", 96, "Writing the YouTube metadata")
        publish_pack = await skills.build_publish_pack(
            topic=request["topic"],
            title=script.get("title", request["topic"]),
            scenes=scenes,
            language=language,
            duration=total_audio,
        )

        video_url, upload_warning = await storage.publish(out_path, f"{job_id}/{out_name}")
        if upload_warning:
            warnings.append(upload_warning)
        subtitle_url, _ = await storage.publish(srt_path, f"{job_id}/subtitles.srt")

        store.update_job(
            job_id,
            status="done",
            step="done",
            progress=100,
            log=f"Finished in {len(scenes)} scenes ({total_audio:.1f}s)",
            result={
                "video_url": video_url,
                "download_url": f"/api/jobs/{job_id}/download",
                "subtitle_url": subtitle_url,
                "duration": round(total_audio, 2),
                "warnings": warnings,
                "style_bible": prompt_pack.get("style_bible"),
                "metadata": publish_pack,
                "scenes": [
                    {
                        "index": s["index"],
                        "narration": s["narration"],
                        "image_prompt": s.get("image_prompt", ""),
                        "motion": s.get("motion"),
                        "start": round(s.get("start", 0.0), 2),
                        "duration": round(s.get("audio_duration", 0.0), 2),
                        "image_url": f"/api/files/{job_id}/images/scene_{s['index']:03d}.png",
                        "hero_ids": s.get("hero_ids", []),
                    }
                    for s in scenes
                ],
            },
        )

    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        detail = str(exc) or exc.__class__.__name__
        store.update_job(
            job_id,
            status="failed",
            step="failed",
            error=detail,
            log=f"Failed: {detail}",
            result={"traceback": traceback.format_exc()[-4000:], "warnings": warnings},
        )
