"""Ken Burns (slow zoom / pan) filter graphs for a single still image.

Two things make the difference between a move that looks like a camera and one
that looks like a slideshow:

1. `zoompan` samples its output from the *input* frame, so a 1080p still zoomed
   to 1.2x would be resampled from fewer than 1080 source lines and visibly
   soften. Scaling the still to 2x the canvas first means every output pixel is
   still a downsample, which is what keeps the motion clean.
2. A linear ramp starts and stops abruptly — the eye reads it as mechanical.
   Easing the progress with a smoothstep curve gives the move a gentle
   acceleration in and deceleration out, the way a real dolly or crane behaves.
"""

from __future__ import annotations

MOTIONS = (
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "zoom_in_pan_right",
    "zoom_out_pan_left",
)

ZOOM_RANGE = 0.20      # how far a zoom travels over the whole scene
PAN_ZOOM = 0.16        # constant zoom held during a pure pan, to leave room to move
DRIFT = 0.035          # tiny counter-move on a pure zoom, so it never feels locked off
SUPERSAMPLE = 2        # render the still at this multiple of the canvas

_CENTER_X = "iw/2-(iw/zoom/2)"
_CENTER_Y = "ih/2-(ih/zoom/2)"


def _eased(frames: int) -> str:
    """Smoothstep progress 0 → 1: p²(3−2p), eased at both ends."""
    p = f"(on/{max(frames - 1, 1)})"
    return f"({p}*{p}*(3-2*{p}))"


def _zoom_in(e: str) -> str:
    return f"(1+{ZOOM_RANGE}*{e})"


def _zoom_out(e: str) -> str:
    return f"({1 + ZOOM_RANGE}-{ZOOM_RANGE}*{e})"


def motion_filter(motion: str, frames: int) -> tuple[str, str, str]:
    """Return the (zoom, x, y) expressions for one motion preset."""
    e = _eased(frames)
    held = f"{1 + PAN_ZOOM}"

    travel_x = "(iw-iw/zoom)"
    travel_y = "(ih-ih/zoom)"
    # A pure zoom also drifts a few percent off-centre, which reads as a camera
    # on a real head rather than a perfectly centred digital crop.
    drift_x = f"{_CENTER_X}+{travel_x}*{DRIFT}*(2*{e}-1)"
    drift_y = f"{_CENTER_Y}+{travel_y}*{DRIFT}*(2*{e}-1)"

    if motion == "zoom_out":
        return _zoom_out(e), drift_x, _CENTER_Y
    if motion == "pan_left":
        return held, f"{travel_x}*(1-{e})", _CENTER_Y
    if motion == "pan_right":
        return held, f"{travel_x}*{e}", _CENTER_Y
    if motion == "pan_up":
        return held, _CENTER_X, f"{travel_y}*(1-{e})"
    if motion == "pan_down":
        return held, _CENTER_X, f"{travel_y}*{e}"
    if motion == "zoom_in_pan_right":
        return _zoom_in(e), f"{travel_x}*{e}", _CENTER_Y
    if motion == "zoom_out_pan_left":
        return _zoom_out(e), f"{travel_x}*(1-{e})", _CENTER_Y
    # default: zoom_in
    return _zoom_in(e), _CENTER_X, drift_y


def build_filter(*, motion: str, frames: int, width: int, height: int, fps: int) -> str:
    """Full video filter chain turning one still into a moving clip."""
    source_w = width * SUPERSAMPLE
    source_h = height * SUPERSAMPLE
    zoom, x, y = motion_filter(motion if motion in MOTIONS else "zoom_in", frames)

    return (
        f"scale={source_w}:{source_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={source_w}:{source_h},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},"
        f"format=yuv420p,setsar=1"
    )
