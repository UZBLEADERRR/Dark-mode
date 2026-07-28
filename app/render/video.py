"""ffmpeg orchestration: stills + narration + captions -> a finished MP4.

Timing is the whole game here. Each scene clip is rendered `TRANSITION` seconds
longer than its narration, and the cross-fades consume exactly that surplus — so
scene *k* lands on the timeline at the cumulative narration time, and image,
voice and subtitle stay locked together no matter how many scenes there are.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .. import config
from . import kenburns, overlays as ov

SCENE_GAP = 0.25  # breath of silence appended to each narration segment

# The rotation used when a scene has no transition of its own. `fade` repeats
# on purpose: a video where every cut is a different flourish looks restless, so
# the plainer one carries most of them.
TRANSITIONS = (
    "fade",
    "smoothleft",
    "fade",
    "circleopen",
    "fade",
    "smoothright",
    "fade",
    "dissolve",
)

# What the scene editor offers — distinct, and each one verified to exist in the
# ffmpeg xfade filter.
TRANSITION_CHOICES = (
    "fade",
    "fadeblack",
    "fadewhite",
    "dissolve",
    "smoothleft",
    "smoothright",
    "smoothup",
    "smoothdown",
    "slideleft",
    "slideright",
    "wipeleft",
    "wiperight",
    "circleopen",
    "circleclose",
    "radial",
    "pixelize",
)


class RenderError(RuntimeError):
    pass


def ffmpeg_available() -> bool:
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


async def _run(args: list[str], cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-25:]
        raise RenderError(f"{args[0]} failed ({process.returncode}):\n" + "\n".join(tail))
    return stdout.decode("utf-8", "replace").strip()


async def probe_duration(path: Path) -> float:
    out = await _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    try:
        return float(out)
    except ValueError as exc:
        raise RenderError(f"Could not read the duration of {path.name}") from exc


async def make_scene_clip(
    *,
    image: Path,
    motion: str,
    duration: float,
    width: int,
    height: int,
    out_path: Path,
    strength: float = 1.0,
    image_overlays: list[dict] | None = None,
) -> Path:
    """Render one still into a moving, silent clip, with its picture layers on top."""
    fps = config.FPS
    frames = max(2, int(round(duration * fps)))
    vf = kenburns.build_filter(
        motion=motion, frames=frames, width=width, height=height, fps=fps, strength=strength
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    tail = [
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]

    layers = [l for l in (image_overlays or []) if l.get("path")]
    if not layers:
        await _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(image), "-vf", vf, *tail,
        ])
        return out_path

    extra_inputs, parts, final = ov.image_chain(
        layers, width=width, height=height, base_label="[bg]", first_input=1
    )
    graph = ";".join([f"[0:v]{vf}[bg]", *parts])

    async def attempt(args: list[str]) -> None:
        await _run(args)

    try:
        await attempt([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(image), *extra_inputs,
            "-filter_complex", graph, "-map", final, *tail,
        ])
    except RenderError:
        # A broken layer must never cost someone the whole video: fall back to
        # the plain scene and let the render finish without it.
        await attempt([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(image), "-vf", vf, *tail,
        ])
    return out_path


async def concat_narration(
    *, audio_paths: list[Path], out_path: Path, gap: float = SCENE_GAP
) -> Path:
    """Join the per-scene voice files, padding each with a short breath."""
    inputs: list[str] = []
    for path in audio_paths:
        inputs += ["-i", str(path)]

    chains = [
        f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"apad=pad_dur={gap}[a{i}]"
        for i in range(len(audio_paths))
    ]
    labels = "".join(f"[a{i}]" for i in range(len(audio_paths)))
    graph = ";".join(chains) + f";{labels}concat=n={len(audio_paths)}:v=0:a=1[out]"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *inputs,
            "-filter_complex", graph,
            "-map", "[out]",
            "-c:a", "pcm_s16le",
            str(out_path),
        ]
    )
    return out_path


def _video_graph(
    clip_count: int,
    durations: list[float],
    transition: float,
    subtitle_file: str | None,
    effects: list[str | None] | None = None,
) -> tuple[str, str]:
    """Chain the clips with cross-fades; return (graph, final video label).

    `effects[i]` names the transition *into* clip i. Anything unset falls back to
    the rotating default, so an untouched video still varies its cuts.
    """
    # A label in the returned pair is what `-map` receives, so a filter output is
    # bracketed and a bare input stream is not — ffmpeg rejects `-map "[0:v]"`.
    if clip_count == 1:
        if subtitle_file:
            return f"[0:v]subtitles={subtitle_file}[vout]", "[vout]"
        return "", "0:v"

    parts: list[str] = []
    current = "[0:v]"
    offset = 0.0
    for i in range(1, clip_count):
        # Validate against the full choice list, not the short rotation — the
        # rotation holds only the handful used as defaults, so checking against
        # it would silently discard every other transition the user picked.
        chosen = effects[i] if effects and i < len(effects) else None
        if chosen not in TRANSITION_CHOICES:
            chosen = TRANSITIONS[i % len(TRANSITIONS)]
        # Clip i-1 runs `transition` seconds past its narration; the fade eats it.
        offset += durations[i - 1] - transition
        label = f"[vx{i}]"
        parts.append(
            f"{current}[{i}:v]xfade=transition={chosen}"
            f":duration={transition}:offset={offset:.3f}{label}"
        )
        current = label

    if subtitle_file:
        parts.append(f"{current}subtitles={subtitle_file}[vout]")
        current = "[vout]"

    return ";".join(parts), current


def _audio_graph(
    narration_index: int, music_index: int | None, total: float, duck: bool
) -> tuple[str, str]:
    if music_index is None:
        # No mixing to do: map the narration input stream straight through.
        return "", f"{narration_index}:a"

    fade_start = max(0.0, total - 2.5)
    voice = (
        f"[{narration_index}:a]aresample=48000,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo"
    )
    bed = (
        f"[{music_index}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"aloop=loop=-1:size=2147483647,atrim=0:{total:.3f},"
        f"volume={config.MUSIC_VOLUME},"
        f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start:.3f}:d=2.5[bed]"
    )

    if duck:
        # A second copy of the voice drives the compressor's sidechain, so the
        # music dips under the narration instead of fighting it.
        parts = [
            f"{voice},asplit=2[narr][key]",
            bed,
            "[bed][key]sidechaincompress=threshold=0.05:ratio=12:attack=15:release=350[bedduck]",
            "[narr][bedduck]amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
            ",alimiter=limit=0.95[aout]",
        ]
    else:
        parts = [
            f"{voice}[narr]",
            bed,
            "[narr][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0"
            ",alimiter=limit=0.95[aout]",
        ]

    return ";".join(parts), "[aout]"


async def assemble(
    *,
    clips: list[Path],
    clip_durations: list[float],
    narration: Path,
    total_duration: float,
    out_path: Path,
    workdir: Path,
    subtitle_file: Path | None = None,
    music: Path | None = None,
    music_start: float = 0.0,
    effects: list[str | None] | None = None,
) -> Path:
    """Cross-fade the clips, burn the captions, mix the audio, write the MP4."""
    if not clips:
        raise RenderError("There are no scene clips to assemble.")

    transition = min(config.TRANSITION_SECONDS, max(0.2, min(clip_durations) / 2))

    async def attempt(with_music: bool, duck: bool) -> Path:
        inputs: list[str] = []
        for clip in clips:
            inputs += ["-i", clip.name]
        inputs += ["-i", narration.name]
        narration_index = len(clips)

        music_index: int | None = None
        if with_music and music is not None:
            # Seeking the input is how the chosen part of the track is picked;
            # the loop filter downstream then repeats from that point, not from
            # the top of the file.
            if music_start > 0:
                inputs += ["-ss", f"{music_start:.3f}"]
            inputs += ["-i", music.name]
            music_index = narration_index + 1

        video_graph, video_label = _video_graph(
            len(clips), clip_durations, transition,
            subtitle_file.name if subtitle_file else None, effects,
        )
        audio_graph, audio_label = _audio_graph(
            narration_index, music_index, total_duration, duck
        )

        graph = ";".join(part for part in (video_graph, audio_graph) if part)

        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs]
        if graph:
            args += ["-filter_complex", graph]
        args += [
            "-map", video_label,
            "-map", audio_label,
            "-t", f"{total_duration:.3f}",
            "-c:v", "libx264",
            "-preset", config.VIDEO_PRESET,
            "-crf", str(config.VIDEO_CRF),
            "-pix_fmt", "yuv420p",
            "-r", str(config.FPS),
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            out_path.name,
        ]
        await _run(args, cwd=workdir)
        return out_path

    if music is not None:
        try:
            return await attempt(with_music=True, duck=True)
        except RenderError:
            # sidechaincompress is the least portable piece — retry flat, then dry.
            try:
                return await attempt(with_music=True, duck=False)
            except RenderError:
                pass
    return await attempt(with_music=False, duck=False)
