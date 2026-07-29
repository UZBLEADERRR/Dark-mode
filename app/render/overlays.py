"""Overlay layers: extra text and extra pictures placed on top of a scene.

An overlay is stored on the scene it belongs to, with times measured from the
start of that scene, so moving or re-timing a scene carries its layers with it.
Two very different renderers consume them:

* **text**  -> extra Dialogue lines in the ASS file (see `subtitles.py`). libass
  already runs for the captions, so a text layer is free.
* **image** -> an ffmpeg `overlay` chain on that one scene's clip, which keeps
  the global assembly graph exactly as simple as it was.

Everything here is deliberately tolerant: a layer that arrives half-filled from
the browser is completed from the defaults rather than rejected, because losing
someone's caption over a missing field would be the worse failure.
"""

from __future__ import annotations

import math

TYPES = ("text", "image")
TEXT_ANIMATIONS = ("none", "fade", "pop", "rise", "slide_left", "slide_right")
IMAGE_ANIMATIONS = ("none", "fade", "pop", "rise", "float", "drift")

# An actor is an image layer that goes somewhere. The still it is cut from never
# changes — nothing is drawn frame by frame — but moving a cut-out across a
# background is what separates a slide from a scene, and it is the whole of what
# paper cut-out animation ever was.
#
# Each entry says where the layer starts and ends, as a multiple of the frame
# width away from the position it was placed at. `enter_left` therefore begins
# off the left edge and arrives where you put it; `exit_right` leaves from there.
ACTOR_MOVES: dict[str, dict] = {
    "walk_right":  {"from": -0.22, "to": 0.22, "label": "O'ngga yuradi"},
    "walk_left":   {"from": 0.22, "to": -0.22, "label": "Chapga yuradi"},
    "enter_left":  {"from": -0.85, "to": 0.0, "label": "Chapdan kiradi"},
    "enter_right": {"from": 0.85, "to": 0.0, "label": "O'ngdan kiradi"},
    "exit_left":   {"from": 0.0, "to": -0.85, "label": "Chapga chiqib ketadi"},
    "exit_right":  {"from": 0.0, "to": 0.85, "label": "O'ngga chiqib ketadi"},
    "cross_right": {"from": -0.85, "to": 0.85, "label": "Chapdan o'ngga o'tadi"},
    "cross_left":  {"from": 0.85, "to": -0.85, "label": "O'ngdan chapga o'tadi"},
    # These two stay put horizontally; the dictionary entry exists so the editor
    # can list every move in one place.
    "hop":         {"from": 0.0, "to": 0.0, "label": "Sakraydi"},
    "sway":        {"from": 0.0, "to": 0.0, "label": "Tebranadi"},
}

# A move that carries the layer across the frame should not also fade at the
# edges: it is meant to arrive from off-screen, not to appear out of nothing.
TRAVELLING = {"enter_left", "enter_right", "exit_left", "exit_right",
              "cross_right", "cross_left"}

MAX_PER_SCENE = 8

DEFAULTS: dict = {
    "type": "text",
    "text": "",
    "asset_id": None,
    "x": 0.5,
    "y": 0.22,
    "size": 0.08,
    "start": 0.0,
    "end": 0.0,          # 0 means "until the scene ends"
    "anim": "fade",
    "colour": "#FFFFFF",
    "outline_colour": "#000000",
    "box": False,
    "box_colour": "#000000",
    "box_opacity": 0.6,
    "bold": True,
    "italic": False,
    "rotate": 0.0,
    "opacity": 1.0,
    "font": "",
    # Set when the Choreographer placed this layer, and to which character. It
    # is what lets a re-staging replace its own work without touching a sticker
    # the user put there by hand.
    "actor_of": "",
}

_NUMERIC = {
    "x": (-0.2, 1.2),
    "y": (-0.2, 1.2),
    "size": (0.02, 1.0),
    "start": (0.0, 3600.0),
    "end": (0.0, 3600.0),
    "rotate": (-180.0, 180.0),
    "opacity": (0.05, 1.0),
    "box_opacity": (0.0, 1.0),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _number(raw: object, key: str) -> float:
    low, high = _NUMERIC[key]
    try:
        return round(_clamp(float(raw), low, high), 4)
    except (TypeError, ValueError):
        return float(DEFAULTS[key])


def normalize(raw: dict, index: int) -> dict | None:
    """Complete and bound one layer. Returns None when there is nothing to draw."""
    if not isinstance(raw, dict):
        return None

    kind = str(raw.get("type") or "text").lower()
    if kind not in TYPES:
        kind = "text"

    layer = dict(DEFAULTS)
    layer.update({k: v for k, v in raw.items() if k in DEFAULTS and v is not None})
    layer["type"] = kind
    layer["id"] = str(raw.get("id") or f"ov{index}")

    for key in _NUMERIC:
        layer[key] = _number(layer.get(key), key)

    allowed = TEXT_ANIMATIONS if kind == "text" else IMAGE_ANIMATIONS + tuple(ACTOR_MOVES)
    if layer["anim"] not in allowed:
        layer["anim"] = "fade"

    for key in ("box", "bold", "italic"):
        layer[key] = bool(layer[key])
    layer["text"] = str(layer["text"])[:180]
    layer["asset_id"] = str(layer["asset_id"]) if layer["asset_id"] else None
    layer["font"] = str(layer.get("font") or "").strip()
    layer["actor_of"] = str(layer.get("actor_of") or "").strip()

    if kind == "text" and not layer["text"].strip():
        return None
    if kind == "image" and not layer["asset_id"]:
        return None
    return layer


def normalize_all(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    # Filter first, cap second: capping the input would let a run of junk
    # entries push out layers the user can actually see.
    for item in raw:
        layer = normalize(item, len(out))
        if layer is not None:
            out.append(layer)
        if len(out) >= MAX_PER_SCENE:
            break
    return out


def window(layer: dict, scene_duration: float) -> tuple[float, float]:
    """Clamp a layer's times into the scene it lives on."""
    span = max(0.4, float(scene_duration))
    start = _clamp(float(layer.get("start", 0.0)), 0.0, max(0.0, span - 0.2))
    end = float(layer.get("end") or 0.0)
    if end <= start:
        end = span
    return start, min(end, span)


def text_layers(scene: dict, offset: float, scene_duration: float) -> list[dict]:
    """Text layers of one scene, moved onto the video's timeline."""
    out = []
    for layer in scene.get("overlays") or []:
        if layer.get("type") != "text":
            continue
        start, end = window(layer, scene_duration)
        out.append({**layer, "start": offset + start, "end": offset + end})
    return out


def image_layers(scene: dict, scene_duration: float) -> list[dict]:
    """Image layers of one scene, timed against that scene's own clip."""
    out = []
    for layer in scene.get("overlays") or []:
        if layer.get("type") != "image":
            continue
        start, end = window(layer, scene_duration)
        out.append({**layer, "start": start, "end": end})
    return out


# ── ffmpeg graph for image layers ─────────────────────────────────────────────

def _even(value: float) -> int:
    """ffmpeg's yuv420p pipeline wants even dimensions."""
    return max(2, int(round(value / 2)) * 2)


def image_chain(
    layers: list[dict], *, width: int, height: int, base_label: str, first_input: int
) -> tuple[list[str], list[str], str]:
    """Build (extra ffmpeg inputs, filter parts, final label) for image layers.

    `layers` must already carry a resolved `path`. Positions are expressed
    against the overlay filter's own variables (`W`/`H` main, `w`/`h` layer), so
    a layer stays put whatever the picture it was scaled to.
    """
    inputs: list[str] = []
    parts: list[str] = []
    current = base_label

    for i, layer in enumerate(layers):
        stream = first_input + i
        inputs += ["-loop", "1", "-i", str(layer["path"])]

        target_w = _even(width * _clamp(float(layer.get("size", 0.25)), 0.02, 1.5))
        chain = [f"format=rgba", f"scale={target_w}:-2:flags=lanczos"]

        rotate = float(layer.get("rotate", 0.0) or 0.0)
        if abs(rotate) > 0.01:
            radians = round(math.radians(rotate), 5)
            chain.append(
                f"rotate={radians}:c=black@0:ow=rotw({radians}):oh=roth({radians})"
            )

        opacity = _clamp(float(layer.get("opacity", 1.0)), 0.05, 1.0)
        if opacity < 0.999:
            chain.append(f"colorchannelmixer=aa={opacity:.3f}")

        start = float(layer["start"])
        end = float(layer["end"])
        animation = str(layer.get("anim", "fade"))
        span = max(0.3, end - start)
        ramp = min(0.4, span / 3)

        if animation in {"fade", "pop", "rise", "float", "drift"} or (
                animation in ACTOR_MOVES and animation not in TRAVELLING):
            # The layer's own clock starts with the clip, so the fades are placed
            # at absolute clip times rather than relative to the layer.
            chain.append(f"fade=t=in:st={start:.3f}:d={ramp:.3f}:alpha=1")
            chain.append(f"fade=t=out:st={max(start, end - ramp):.3f}:d={ramp:.3f}:alpha=1")

        parts.append(f"[{stream}:v]{','.join(chain)}[ov{i}]")

        base_x = f"(W*{_clamp(float(layer.get('x', 0.5)), -0.2, 1.2):.4f}-w/2)"
        base_y = f"(H*{_clamp(float(layer.get('y', 0.5)), -0.2, 1.2):.4f}-h/2)"
        x_expr, y_expr = base_x, base_y

        if animation == "rise":
            lift = max(12, int(height * 0.05))
            y_expr = f"{base_y}+{lift}*(1-min(1,max(0,(t-{start:.3f})/0.45)))"
        elif animation == "pop":
            # A true scale-up needs a per-frame resize, which `scale` cannot do;
            # a short settle downward reads as the same little arrival.
            lift = max(6, int(height * 0.018))
            y_expr = f"{base_y}-{lift}*(1-min(1,max(0,(t-{start:.3f})/0.3)))"
        elif animation == "float":
            amp = max(6, int(height * 0.014))
            y_expr = f"{base_y}+{amp}*sin(2*PI*(t-{start:.3f})/3.2)"
        elif animation == "drift":
            travel = max(10, int(width * 0.05))
            x_expr = f"{base_x}+{travel}*(t-{start:.3f})/{span:.3f}"
        elif animation in ACTOR_MOVES:
            move = ACTOR_MOVES[animation]
            # `p` is how far through the layer's own window we are, 0 to 1, held
            # at the ends so an actor that has arrived stays arrived rather than
            # sliding on past.
            p = f"min(1,max(0,(t-{start:.3f})/{span:.3f}))"
            begin = int(width * move["from"])
            finish = int(width * move["to"])
            if begin != finish:
                # Eased rather than linear: a cut-out that starts and stops
                # abruptly reads as a sliding sticker, not as something walking.
                ease = f"({p}*{p}*(3-2*{p}))"
                x_expr = f"{base_x}+{begin}+{finish - begin}*{ease}"
            if animation == "hop":
                # Two bounces across the window, never below the ground line.
                lift = max(8, int(height * 0.05))
                x_expr = f"{base_x}"
                y_expr = f"{base_y}-{lift}*abs(sin(2*PI*(t-{start:.3f})/{max(0.9, span / 2):.3f}))"
            elif animation == "sway":
                tilt = max(4, int(width * 0.006))
                x_expr = f"{base_x}+{tilt}*sin(2*PI*(t-{start:.3f})/2.4)"
            elif animation in {"walk_right", "walk_left", "cross_right", "cross_left"}:
                # A walking figure rises and falls a little on each step. It is a
                # small thing and it is most of what sells the movement.
                step = max(3, int(height * 0.008))
                y_expr = f"{base_y}-{step}*abs(sin(2*PI*(t-{start:.3f})/0.65))"

        label = f"[ovout{i}]"
        parts.append(
            f"{current}[ov{i}]overlay=x='{x_expr}':y='{y_expr}'"
            f":enable='between(t,{start:.3f},{end:.3f})':eval=frame{label}"
        )
        current = label

    return inputs, parts, current
