"""Keeping a scene's picture the size the render can actually use.

A still is animated by `zoompan`, which samples every output pixel from a copy
scaled to a fixed multiple of the canvas — 1.7× at the default speed, 3× at the
very most. Anything larger than that arrives, is decoded in full on every frame,
and is then thrown away down to the same size it would have been anyway.

The cost is not theoretical. ffmpeg holds the decoded frame, the scaled copy and
the zoompan buffer at once, so a picture four times the canvas costs sixteen
times the pixels — per encoder, and a render runs several at a time. It is the
one thing in a render that is unbounded from the outside: a generated still is
whatever the provider felt like returning, and an uploaded one is whatever came
off somebody's camera.
"""

from __future__ import annotations

from pathlib import Path

# How far above the canvas a stored still is allowed to be, on its longest side.
# Above `SUPERSAMPLE` (3.0 at the absolute most) nothing survives the animation,
# so this is that with room to spare rather than a quality judgement.
HEADROOM = 2.0

# Below this there is nothing to gain and a re-encode to lose, so a picture that
# is already a sensible size is not touched at all.
SLACK = 1.15


def too_big(size: tuple[int, int], canvas: tuple[int, int]) -> bool:
    width, height = size
    limit_w, limit_h = canvas[0] * HEADROOM * SLACK, canvas[1] * HEADROOM * SLACK
    return width > limit_w or height > limit_h


def fit(path: Path, canvas: tuple[int, int]) -> tuple[int, int] | None:
    """Shrink an oversized still in place. Returns the new size, or None.

    Never raises: a picture that cannot be read is the drawing stage's problem
    to report, and failing here would lose a scene over an optimisation.
    """
    try:
        from PIL import Image

        with Image.open(path) as img:
            size = img.size
            if not too_big(size, canvas):
                return None
            scale = min(canvas[0] * HEADROOM / size[0], canvas[1] * HEADROOM / size[1])
            wanted = (max(2, int(size[0] * scale)), max(2, int(size[1] * scale)))
            # `Image.LANCZOS` under its modern name, with the old one as the
            # fallback so an older Pillow in some deployment still works.
            resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
            shrunk = img.convert("RGB").resize(wanted, resample)
        # Written through a temporary name: a render that dies mid-save would
        # otherwise leave a half-written picture where a whole one had been.
        part = path.with_suffix(path.suffix + ".part")
        shrunk.save(part, format="PNG")
        part.replace(path)
        return wanted
    except Exception:  # noqa: BLE001 - a picture kept too large is not a failure
        return None
