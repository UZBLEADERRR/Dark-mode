"""Ken Burns (slow zoom / pan) filter graphs for a single still image.

`zoompan` samples its output from the *input* frame, so a 1080p still zoomed to
1.2x would be resampled from fewer than 1080 source lines and visibly soften.
Scaling the still to 2x the canvas first means every output pixel is still
downsampled, which is what keeps the motion clean instead of mushy.
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

ZOOM_RANGE = 0.22      # how far a zoom travels over the whole scene
PAN_ZOOM = 0.18        # constant zoom held during a pure pan, to leave room to move
SUPERSAMPLE = 2        # render the still at this multiple of the canvas

_CENTER_X = "iw/2-(iw/zoom/2)"
_CENTER_Y = "ih/2-(ih/zoom/2)"


def _progress(frames: int) -> str:
    """0 → 1 across the scene. `on` is the output frame index."""
    return f"on/{max(frames - 1, 1)}"


def _zoom_in(p: str) -> str:
    return f"1+{ZOOM_RANGE}*({p})"


def _zoom_out(p: str) -> str:
    return f"{1 + ZOOM_RANGE}-{ZOOM_RANGE}*({p})"


def motion_filter(motion: str, frames: int) -> tuple[str, str, str]:
    """Return the (zoom, x, y) expressions for one motion preset."""
    p = _progress(frames)
    held = f"{1 + PAN_ZOOM}"

    travel_x = "(iw-iw/zoom)"
    travel_y = "(ih-ih/zoom)"

    if motion == "zoom_out":
        return _zoom_out(p), _CENTER_X, _CENTER_Y
    if motion == "pan_left":
        return held, f"{travel_x}*(1-({p}))", _CENTER_Y
    if motion == "pan_right":
        return held, f"{travel_x}*({p})", _CENTER_Y
    if motion == "pan_up":
        return held, _CENTER_X, f"{travel_y}*(1-({p}))"
    if motion == "pan_down":
        return held, _CENTER_X, f"{travel_y}*({p})"
    if motion == "zoom_in_pan_right":
        return _zoom_in(p), f"{travel_x}*({p})", _CENTER_Y
    if motion == "zoom_out_pan_left":
        return _zoom_out(p), f"{travel_x}*(1-({p}))", _CENTER_Y
    # default: zoom_in
    return _zoom_in(p), _CENTER_X, _CENTER_Y


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
