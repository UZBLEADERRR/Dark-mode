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


def _shot_graph(
    cuts: list[dict], width: int, height: int, fps: int, inner: float,
    supersample: float,
) -> tuple[str, str, int]:
    """Animate each shot and join them; return (graph, label, total frames).

    `cuts[i]` needs `seconds`, `motion`, `motion_strength` and optionally
    `transition`. A shot with no transition is a straight cut, which is what
    fast cutting normally wants; anything else cross-fades in, and the fade eats
    the overlap the slices were grown to pay for.
    """
    parts: list[str] = []
    frames = [max(2, int(round(float(c["seconds"]) * fps))) for c in cuts]

    for i, cut in enumerate(cuts):
        vf = kenburns.build_filter(
            motion=cut.get("motion", "zoom_in"), frames=frames[i],
            width=width, height=height, fps=fps,
            strength=float(cut.get("motion_strength") or 1.0), supersample=supersample,
        )
        # setpts restarts each shot's clock at zero. Without it xfade reads the
        # offsets against whatever timestamps the still came in with.
        parts.append(f"[{i}:v]{vf},setpts=PTS-STARTPTS[sh{i}]")

    if len(cuts) == 1:
        return ";".join(parts), "[sh0]", frames[0]

    fades = [(c.get("transition") or "").strip() for c in cuts]
    if not any(f in TRANSITION_CHOICES for f in fades[1:]):
        # Every join is a hard cut, so the frames simply add up.
        joined = "".join(f"[sh{i}]" for i in range(len(cuts)))
        parts.append(f"{joined}concat=n={len(cuts)}:v=1:a=0[shots]")
        return ";".join(parts), "[shots]", sum(frames)

    current = "[sh0]"
    offset = 0.0
    for i in range(1, len(cuts)):
        chosen = fades[i] if fades[i] in TRANSITION_CHOICES else "fade"
        offset += float(cuts[i - 1]["seconds"]) - inner
        label = f"[shx{i}]"
        parts.append(
            f"{current}[sh{i}]xfade=transition={chosen}"
            f":duration={inner}:offset={offset:.3f}{label}"
        )
        current = label

    total = sum(float(c["seconds"]) for c in cuts) - inner * (len(cuts) - 1)
    return ";".join(parts), current, max(2, int(round(total * fps)))


async def make_scene_clip(
    *,
    duration: float,
    width: int,
    height: int,
    out_path: Path,
    shots: list[dict] | None = None,
    image: Path | None = None,
    motion: str = "zoom_in",
    strength: float = 1.0,
    inner_transition: float = 0.0,
    image_overlays: list[dict] | None = None,
    speed: dict | None = None,
) -> Path:
    """Render a scene's picture: one still or several, animated and joined.

    Multi-shot scenes are built in a single ffmpeg pass — animate, join, then
    lay the overlays on top — so cutting a scene into three costs one encode
    rather than four, and the overlays still see the whole scene rather than
    whichever shot they happen to land on.
    """
    fps = config.FPS
    profile = speed or config.speed_profile()
    cuts = [dict(s) for s in (shots or []) if s.get("image")]
    if not cuts:
        if image is None:
            raise RenderError("A scene clip needs at least one picture.")
        cuts = [{"image": image, "motion": motion, "motion_strength": strength,
                 "seconds": duration}]
    if len(cuts) == 1:
        cuts[0]["seconds"] = duration

    inner = inner_transition if len(cuts) > 1 else 0.0
    graph, label, frames = _shot_graph(
        cuts, width, height, fps, inner, profile["supersample"])
    inputs: list[str] = []
    for cut in cuts:
        inputs += ["-i", str(cut["image"])]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tail = [
        "-frames:v", str(frames),
        "-r", str(fps),
        "-c:v", "libx264",
        "-preset", profile["clip_preset"],
        # This file is only ever an input to the final encode, so it is cheap
        # rather than small — a high bitrate here costs disk, not quality.
        "-crf", str(profile["clip_crf"]),
        "-threads", str(profile["threads"]),
        "-pix_fmt", "yuv420p",
        str(out_path),
    ]

    async def render(with_layers: bool) -> None:
        parts = [graph]
        final = label
        extra: list[str] = []
        if with_layers:
            extra, chain, final = ov.image_chain(
                layers, width=width, height=height, base_label=label,
                first_input=len(cuts),
            )
            parts += chain
        await _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            *inputs, *extra,
            "-filter_complex", ";".join(p for p in parts if p),
            "-map", final, *tail,
        ])

    layers = [l for l in (image_overlays or []) if l.get("path")]
    if not layers:
        await render(with_layers=False)
        return out_path

    try:
        await render(with_layers=True)
    except RenderError:
        # A broken layer must never cost someone the whole video: fall back to
        # the plain scene and let the render finish without it.
        await render(with_layers=False)
    return out_path


# ── dubbing an existing video ─────────────────────────────────────────────────

async def probe_video(path: Path) -> dict:
    """Duration plus the dimensions, for a file the user handed us."""
    out = await _run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1", str(path),
    ])
    info: dict = {}
    for line in out.splitlines():
        key, _, value = line.partition("=")
        try:
            info[key] = float(value) if key == "duration" else int(value)
        except ValueError:
            pass
    if "duration" not in info:
        raise RenderError("Could not read the length of that video.")
    return info


async def extract_audio(video_path: Path, out_path: Path) -> Path:
    """Pull the soundtrack out as small mono audio a transcriber will accept."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
        str(out_path),
    ])
    return out_path


def _tempo_chain(factor: float) -> str:
    """`atempo` only accepts 0.5–2.0, so a bigger change is chained."""
    factor = max(0.25, min(4.0, factor))
    steps: list[float] = []
    while factor < 0.5:
        steps.append(0.5)
        factor /= 0.5
    while factor > 2.0:
        steps.append(2.0)
        factor /= 2.0
    steps.append(factor)
    return ",".join(f"atempo={s:.4f}" for s in steps)


async def fit_speech(
    *, source: Path, out_path: Path, speech: float, slot: float,
    min_tempo: float = 0.75, max_tempo: float = 1.45,
) -> Path:
    """Squeeze one spoken line into the slot the original occupied.

    A translation is rarely the same length as what it replaces, so the speech is
    gently sped up or slowed down to fit — bounded, because past about 1.5x it
    stops sounding like a person — and the remainder of the slot is silence, so
    the next line still starts exactly where it did in the original.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    actual = await probe_duration(source)
    target = max(0.2, min(speech, slot))
    tempo = max(min_tempo, min(max_tempo, actual / target)) if target > 0 else 1.0

    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-af", f"{_tempo_chain(tempo)},aresample=48000,"
               f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
               f"apad=whole_dur={slot:.3f},atrim=0:{slot:.3f}",
        "-c:a", "pcm_s16le", str(out_path),
    ])
    return out_path


async def silence(out_path: Path, seconds: float) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{max(0.01, seconds):.3f}", "-c:a", "pcm_s16le", str(out_path),
    ])
    return out_path


async def concat_audio(pieces: list[Path], out_path: Path) -> Path:
    """Join pieces end to end. Each already has its exact length, so the join
    alone places every line at the second it belongs on."""
    if not pieces:
        raise RenderError("There is nothing to join.")
    inputs: list[str] = []
    for piece in pieces:
        inputs += ["-i", str(piece)]
    labels = "".join(f"[{i}:a]" for i in range(len(pieces)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
        "-filter_complex", f"{labels}concat=n={len(pieces)}:v=0:a=1[out]",
        "-map", "[out]", "-c:a", "pcm_s16le", str(out_path),
    ])
    return out_path


async def mux_dub(
    *, video_path: Path, dub: Path, out_path: Path,
    original_volume: float = 0.0, subtitle_file: Path | None = None,
    speed: dict | None = None,
) -> Path:
    """Put the new soundtrack on the original picture.

    Without burned-in subtitles the video stream is copied rather than
    re-encoded, so dubbing a ten-minute film is a matter of seconds.
    """
    profile = speed or config.speed_profile()
    # cwd is the project folder purely so the subtitle file can be a bare name in
    # the filter graph, where an absolute path would need its colons escaped. The
    # inputs stay absolute: the source video was uploaded elsewhere.
    workdir = out_path.parent
    inputs = ["-i", str(video_path.resolve()), "-i", str(dub.resolve())]

    keep = max(0.0, min(1.0, original_volume))
    if keep > 0.01:
        audio_graph = (
            f"[0:a]volume={keep:.3f},aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo[bed];"
            "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[dub];"
            "[dub][bed]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_graph = ""
        audio_map = "1:a"

    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs]
    if subtitle_file:
        graph = f"[0:v]subtitles={subtitle_file.name}[vout]"
        if audio_graph:
            graph += ";" + audio_graph
        args += ["-filter_complex", graph, "-map", "[vout]", "-map", audio_map,
                 "-c:v", "libx264", "-preset", profile["final_preset"],
                 "-crf", str(profile["final_crf"]), "-pix_fmt", "yuv420p"]
    else:
        if audio_graph:
            args += ["-filter_complex", audio_graph]
        args += ["-map", "0:v", "-map", audio_map, "-c:v", "copy"]

    args += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-shortest", "-movflags", "+faststart", out_path.name]
    await _run(args, cwd=workdir)
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
    narration_index: int,
    music_index: int | None,
    total: float,
    duck: bool,
    sfx: list[dict] | None = None,
) -> tuple[str, str]:
    """Mix narration, an optional music bed and any one-shot stings.

    `sfx[i]` needs `index` (its ffmpeg input), `at` (seconds into the video) and
    `volume`. Each is delayed to its cue with `adelay` and mixed in; the voice is
    always the first input to `amix`, so `duration=first` keeps the output the
    length of the narration no matter how long a sting runs.
    """
    sfx = sfx or []
    if music_index is None and not sfx:
        # No mixing to do: map the narration input stream straight through.
        return "", f"{narration_index}:a"

    stereo = "aformat=sample_fmts=fltp:channel_layouts=stereo"
    voice = f"[{narration_index}:a]aresample=48000,{stereo}"
    parts: list[str] = []
    mix_labels: list[str] = []

    if music_index is None:
        parts.append(f"{voice}[narr]")
    elif duck:
        # A second copy of the voice drives the compressor's sidechain, so the
        # music dips under the narration instead of fighting it.
        parts.append(f"{voice},asplit=2[narr][key]")
    else:
        parts.append(f"{voice}[narr]")
    mix_labels.append("[narr]")

    if music_index is not None:
        fade_start = max(0.0, total - 2.5)
        parts.append(
            f"[{music_index}:a]aresample=48000,{stereo},"
            f"aloop=loop=-1:size=2147483647,atrim=0:{total:.3f},"
            f"volume={config.MUSIC_VOLUME},"
            f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_start:.3f}:d=2.5[bed]"
        )
        if duck:
            parts.append(
                "[bed][key]sidechaincompress=threshold=0.05:ratio=12:attack=15:release=350[bedduck]"
            )
            mix_labels.append("[bedduck]")
        else:
            mix_labels.append("[bed]")

    for i, cue in enumerate(sfx):
        delay = max(0, int(round(float(cue.get("at", 0.0)) * 1000)))
        volume = max(0.0, min(4.0, float(cue.get("volume", 1.0))))
        parts.append(
            f"[{cue['index']}:a]aresample=48000,{stereo},"
            f"volume={volume:.3f},adelay={delay}|{delay},"
            f"atrim=0:{total:.3f}[sfx{i}]"
        )
        mix_labels.append(f"[sfx{i}]")

    if len(mix_labels) == 1:
        parts.append("[narr]alimiter=limit=0.95[aout]")
    else:
        parts.append(
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first"
            ":dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]"
        )

    return ";".join(parts), "[aout]"


async def remix_audio(
    *,
    video_path: Path,
    narration: Path,
    out_path: Path,
    workdir: Path,
    total_duration: float,
    music: Path | None = None,
    music_start: float = 0.0,
    sfx: list[dict] | None = None,
) -> Path:
    """Rebuild a finished video's soundtrack without touching its pictures.

    The music is mixed against the narration the render kept, never against the
    finished file's own audio — mixing onto that would stack a second bed on top
    of the first, and each pass would layer another. Because only the audio is
    rebuilt, the video stream is copied through: seconds instead of minutes, and
    the picture is bit-for-bit what was approved.
    """
    cues = [c for c in (sfx or []) if c.get("path")]

    async def attempt(with_music: bool, duck: bool, with_sfx: bool = True) -> Path:
        # The source video and the narration are addressed absolutely: they are
        # not always siblings, and only the output is written into `workdir`.
        inputs = ["-i", str(video_path.resolve()), "-i", str(narration.resolve())]
        narration_index = 1
        next_index = 2

        music_index: int | None = None
        if with_music and music is not None:
            if music_start > 0:
                inputs += ["-ss", f"{music_start:.3f}"]
            inputs += ["-i", str(music.resolve())]
            music_index = next_index
            next_index += 1

        staged: list[dict] = []
        if with_sfx:
            for cue in cues:
                inputs += ["-i", str(Path(cue["path"]).resolve())]
                staged.append({**cue, "index": next_index})
                next_index += 1

        graph, audio_label = _audio_graph(
            narration_index, music_index, total_duration, duck, staged)

        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs]
        if graph:
            args += ["-filter_complex", graph]
        args += [
            "-map", "0:v:0",
            "-map", audio_label,
            "-t", f"{total_duration:.3f}",
            "-c:v", "copy",
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
    try:
        return await attempt(with_music=False, duck=False)
    except RenderError:
        if not cues:
            raise
        return await attempt(with_music=False, duck=False, with_sfx=False)


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
    sfx: list[dict] | None = None,
    speed: dict | None = None,
) -> Path:
    """Cross-fade the clips, burn the captions, mix the audio, write the MP4.

    `sfx[i]` is `{"path": Path, "at": seconds, "volume": float}` — a one-shot
    sting cued at an absolute point on the timeline.
    """
    if not clips:
        raise RenderError("There are no scene clips to assemble.")

    transition = min(config.TRANSITION_SECONDS, max(0.2, min(clip_durations) / 2))
    cues = [c for c in (sfx or []) if c.get("path")]
    profile = speed or config.speed_profile()

    async def attempt(with_music: bool, duck: bool, with_sfx: bool = True) -> Path:
        inputs: list[str] = []
        for clip in clips:
            inputs += ["-i", clip.name]
        inputs += ["-i", narration.name]
        narration_index = len(clips)
        next_index = narration_index + 1

        music_index: int | None = None
        if with_music and music is not None:
            # Seeking the input is how the chosen part of the track is picked;
            # the loop filter downstream then repeats from that point, not from
            # the top of the file.
            if music_start > 0:
                inputs += ["-ss", f"{music_start:.3f}"]
            inputs += ["-i", music.name]
            music_index = next_index
            next_index += 1

        staged: list[dict] = []
        if with_sfx:
            for cue in cues:
                inputs += ["-i", Path(cue["path"]).name]
                staged.append({**cue, "index": next_index})
                next_index += 1

        video_graph, video_label = _video_graph(
            len(clips), clip_durations, transition,
            subtitle_file.name if subtitle_file else None, effects,
        )
        audio_graph, audio_label = _audio_graph(
            narration_index, music_index, total_duration, duck, staged
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
            "-preset", profile["final_preset"],
            "-crf", str(profile["final_crf"]),
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
    try:
        return await attempt(with_music=False, duck=False)
    except RenderError:
        if not cues:
            raise
        # Losing the stings is a far smaller failure than losing the video.
        return await attempt(with_music=False, duck=False, with_sfx=False)


async def slice_audio(source: Path, out_path: Path, start: float, end: float) -> Path:
    """Cut one span out of a longer recording, sample-accurately.

    The span is re-encoded rather than stream-copied: a copy can only cut on a
    frame boundary, which for MP3 is up to 26ms out and would drift the picture
    off the words it is cut to. These files are inputs to the final encode
    anyway, so decoding once here costs nothing that survives to the output.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        # After -i, not before: seeking the input would land on the nearest
        # frame and quietly shift every timing that follows.
        "-ss", f"{max(0.0, start):.3f}", "-to", f"{max(start + 0.05, end):.3f}",
        "-ar", "48000", "-ac", "1",
        str(out_path),
    ])
    return out_path


# The colour a cut-out is generated against before it is keyed away. Magenta
# because almost nothing in a drawn character is that hue — green fights foliage
# and clothing, blue fights skies and eyes, and either one eats part of the
# subject along with the background.
CHROMA = "0xFF00FF"


async def cut_out(source: Path, out_path: Path, *, similarity: float = 0.24,
                  blend: float = 0.08) -> Path:
    """Key a flat background colour away, leaving the subject on transparency.

    The generator is asked for the character alone on a solid magenta field, so
    all that is needed here is to remove that field. `blend` is what softens the
    boundary: with a hard key every edge pixel is either fully kept or fully
    dropped, and a cut-out with a one-pixel staircase around it looks pasted on
    rather than drawn. ffmpeg's `despill` only knows green and blue, so the
    blend is doing this job alone — which is why it is a little wider here than
    a green-screen key would use.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    await _run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vf", f"format=rgba,colorkey={CHROMA}:{similarity}:{blend},format=rgba",
        "-frames:v", "1",
        str(out_path),
    ])
    return out_path


async def alpha_coverage(path: Path) -> float:
    """How much of this picture is opaque, 0 to 1.

    An RGBA pixel format proves nothing — `cut_out` always writes RGBA, whether
    or not it removed anything — so the alpha channel is averaged instead. The
    whole image is collapsed to a single pixel by ffmpeg and that one byte is
    read back, which costs one decode and no image library.
    """
    probe = path.with_name(f"{path.stem}-alpha.raw")
    try:
        await _run([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
            # `area` is the one scaler that is a plain average; the default
            # would weight the centre and report the wrong number.
            "-vf", "format=rgba,alphaextract,scale=1:1:flags=area", "-frames:v", "1",
            "-f", "rawvideo", "-pix_fmt", "gray", str(probe),
        ])
        data = probe.read_bytes()
    except (RenderError, OSError):
        return 1.0
    finally:
        probe.unlink(missing_ok=True)
    return (data[0] / 255.0) if data else 1.0


async def has_alpha(path: Path) -> bool:
    """Did the key actually cut something out — without eating the subject?

    Both ends matter. A picture whose background never keyed away comes back
    fully opaque and would be pasted onto the scene as a rectangle; one where
    the key took the character too comes back nearly empty. Either way the
    caller should say so rather than hand back a cut-out that is not one.
    """
    coverage = await alpha_coverage(path)
    return 0.02 < coverage < 0.97
