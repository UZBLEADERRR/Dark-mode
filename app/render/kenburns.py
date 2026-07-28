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

`strength` scales how far every move travels, so the same preset can be a barely
perceptible drift on a talking-head still and a real push on a landscape.
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
    "zoom_in_pan_left",
    "zoom_out_pan_right",
    "diag_up_right",
    "diag_down_left",
    "pulse",
    "sway",
    "still",
)

ZOOM_RANGE = 0.20      # how far a zoom travels over the whole scene
PAN_ZOOM = 0.16        # constant zoom held during a pure pan, to leave room to move
DRIFT = 0.035          # tiny counter-move on a pure zoom, so it never feels locked off
SUPERSAMPLE = 2        # render the still at this multiple of the canvas
SWAY_DEGREES = 0.75    # peak tilt of the `sway` preset
SWAY_MARGIN = 1.08     # upscale before rotating, so the corners never show

_CENTER_X = "iw/2-(iw/zoom/2)"
_CENTER_Y = "ih/2-(ih/zoom/2)"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _eased(frames: int) -> str:
    """Smoothstep progress 0 → 1: p²(3−2p), eased at both ends."""
    p = f"(on/{max(frames - 1, 1)})"
    return f"({p}*{p}*(3-2*{p}))"


def _zoom_in(e: str, span: float) -> str:
    return f"(1+{span:.4f}*{e})"


def _zoom_out(e: str, span: float) -> str:
    return f"({1 + span:.4f}-{span:.4f}*{e})"


def _pan(travel: str, e: str, span: float, reverse: bool = False) -> str:
    """Travel a `span` fraction of the available room, centred on the frame.

    At full strength the move sweeps edge to edge; at half it uses the middle
    half, so a gentler setting stays on the subject instead of starting somewhere
    else entirely.
    """
    span = _clamp(span, 0.15, 1.0)
    origin = (1 - span) / 2
    progress = f"(1-{e})" if reverse else e
    return f"{travel}*({origin:.4f}+{span:.4f}*{progress})"


def motion_filter(motion: str, frames: int, strength: float = 1.0) -> tuple[str, str, str]:
    """Return the (zoom, x, y) expressions for one motion preset."""
    e = _eased(frames)
    strength = _clamp(float(strength or 1.0), 0.3, 1.8)
    zoom_span = ZOOM_RANGE * strength
    pan_span = _clamp(strength, 0.3, 1.0)
    held = f"{1 + PAN_ZOOM}"

    travel_x = "(iw-iw/zoom)"
    travel_y = "(ih-ih/zoom)"
    # A pure zoom also drifts a few percent off-centre, which reads as a camera
    # on a real head rather than a perfectly centred digital crop.
    drift = DRIFT * strength
    drift_x = f"{_CENTER_X}+{travel_x}*{drift:.4f}*(2*{e}-1)"
    drift_y = f"{_CENTER_Y}+{travel_y}*{drift:.4f}*(2*{e}-1)"

    if motion == "still":
        return "1.0", _CENTER_X, _CENTER_Y
    if motion == "pulse":
        # One slow breath in and back out — a heartbeat rather than a move.
        p = f"(on/{max(frames - 1, 1)})"
        return f"(1+{zoom_span * 0.6:.4f}*sin(PI*{p}))", _CENTER_X, _CENTER_Y
    if motion == "sway":
        return held, drift_x, _CENTER_Y
    if motion == "zoom_out":
        return _zoom_out(e, zoom_span), drift_x, _CENTER_Y
    if motion == "pan_left":
        return held, _pan(travel_x, e, pan_span, reverse=True), _CENTER_Y
    if motion == "pan_right":
        return held, _pan(travel_x, e, pan_span), _CENTER_Y
    if motion == "pan_up":
        return held, _CENTER_X, _pan(travel_y, e, pan_span, reverse=True)
    if motion == "pan_down":
        return held, _CENTER_X, _pan(travel_y, e, pan_span)
    if motion == "zoom_in_pan_right":
        return _zoom_in(e, zoom_span), _pan(travel_x, e, pan_span), _CENTER_Y
    if motion == "zoom_in_pan_left":
        return _zoom_in(e, zoom_span), _pan(travel_x, e, pan_span, reverse=True), _CENTER_Y
    if motion == "zoom_out_pan_left":
        return _zoom_out(e, zoom_span), _pan(travel_x, e, pan_span, reverse=True), _CENTER_Y
    if motion == "zoom_out_pan_right":
        return _zoom_out(e, zoom_span), _pan(travel_x, e, pan_span), _CENTER_Y
    if motion == "diag_up_right":
        return _zoom_in(e, zoom_span * 0.6), _pan(travel_x, e, pan_span), \
            _pan(travel_y, e, pan_span, reverse=True)
    if motion == "diag_down_left":
        return _zoom_in(e, zoom_span * 0.6), _pan(travel_x, e, pan_span, reverse=True), \
            _pan(travel_y, e, pan_span)
    # default: zoom_in
    return _zoom_in(e, zoom_span), _CENTER_X, drift_y


def build_filter(
    *, motion: str, frames: int, width: int, height: int, fps: int, strength: float = 1.0
) -> str:
    """Full video filter chain turning one still into a moving clip."""
    source_w = width * SUPERSAMPLE
    source_h = height * SUPERSAMPLE
    motion = motion if motion in MOTIONS else "zoom_in"
    zoom, x, y = motion_filter(motion, frames, strength)

    chain = (
        f"scale={source_w}:{source_h}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={source_w}:{source_h},"
        f"zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps}"
    )

    if motion == "sway":
        # Rotating a frame that is already exactly the canvas size would expose
        # black wedges in the corners, so the tilt happens on an oversized frame
        # and the centre crop throws the wedges away.
        pad_w = int(width * SWAY_MARGIN) // 2 * 2
        pad_h = int(height * SWAY_MARGIN) // 2 * 2
        angle = round(SWAY_DEGREES * _clamp(strength, 0.3, 1.8) * 3.14159 / 180, 5)
        chain += (
            f",scale={pad_w}:{pad_h}:flags=lanczos"
            f",rotate=a='{angle}*sin(2*PI*t/7)':c=black:ow=iw:oh=ih"
            f",crop={width}:{height}"
        )

    return chain + ",format=yuv420p,setsar=1"
