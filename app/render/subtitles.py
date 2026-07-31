"""ASS subtitle and on-screen-text generation.

ASS rather than SRT because it carries the styling — colour, outline weight,
background box, position, fades, per-word karaoke highlighting — inside the file,
so libass burns exactly what was designed instead of ffmpeg's defaults.

The same file also carries every *text* overlay in the video. Anything that is
just glyphs on the frame is cheaper and sharper drawn by libass than by an
ffmpeg drawtext chain, and it costs no extra input, no extra pass and no extra
encode.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import config

# Every knob a caption can be given. A template is a patch on top of this, and a
# user's overrides are a patch on top of the template — so a half-filled style
# dict from the browser is always a complete style by the time it reaches libass.
BASE_STYLE: dict = {
    "template": "bold",
    "colour": "#FFFFFF",          # the text itself
    "highlight": "#FFD84D",       # karaoke: colour a word turns as it is spoken
    "outline_colour": "#000000",
    "outline": 4.0,               # stroke width, or box padding when box="box"
    "shadow": 1.6,
    "box": "outline",             # none | outline | shadow | box
    "box_colour": "#000000",
    "box_opacity": 0.62,
    "bold": True,
    "italic": False,
    "size": 1.0,                  # multiplier on the size the canvas can afford
    "position": "bottom",         # bottom | middle | top
    "margin": 0.0,                # nudge, as a fraction of frame height
    "uppercase": False,
    "animation": "fade",          # none | fade | pop | rise
    "karaoke": False,
    "font": "",
}

# CapCut-style presets. Each is a complete look someone would actually pick, not
# a knob — the knobs are underneath for when the preset is nearly right.
CAPTION_TEMPLATES: dict[str, dict] = {
    "bold": {
        "label": "Qalin",
        "outline": 4.5, "shadow": 2.0, "box": "outline",
    },
    "clean": {
        "label": "Toza",
        "colour": "#FFFFFF", "box": "box", "box_colour": "#0B0B0F",
        "box_opacity": 0.72, "outline": 2.4, "shadow": 0.0,
        "bold": False, "size": 0.9,
    },
    "karaoke": {
        "label": "Karaoke",
        "karaoke": True, "highlight": "#FFD84D", "colour": "#FFFFFF",
        "outline": 4.5, "shadow": 2.0,
    },
    "neon": {
        "label": "Neon",
        "colour": "#FFFFFF", "outline_colour": "#12E5FF", "outline": 3.4,
        "shadow": 4.5, "box": "shadow", "box_colour": "#12E5FF",
        "animation": "pop",
    },
    "boxed": {
        "label": "Fonli",
        "box": "box", "box_colour": "#000000", "box_opacity": 0.8,
        "outline": 3.0, "shadow": 0.0, "size": 0.94,
    },
    "pop": {
        "label": "Pop",
        "colour": "#FFE94A", "outline_colour": "#101010", "outline": 5.5,
        "shadow": 1.2, "uppercase": True, "animation": "pop", "size": 1.06,
    },
    "word": {
        "label": "So'zma-so'z",
        "karaoke": True, "colour": "#FFFFFF", "highlight": "#35F0A0",
        "box": "box", "box_colour": "#08090C", "box_opacity": 0.58,
        "outline": 2.6, "shadow": 0.0, "animation": "pop",
    },
    "minimal": {
        "label": "Nozik",
        "colour": "#FFFFFF", "outline": 0.0, "shadow": 1.4, "box": "shadow",
        "bold": False, "size": 0.84, "animation": "fade",
    },
}

POSITIONS = ("bottom", "middle", "top")
ANIMATIONS = ("none", "fade", "pop", "rise")
BOXES = ("none", "outline", "shadow", "box")

_NUMERIC = {
    "outline": (0.0, 12.0),
    "shadow": (0.0, 12.0),
    "box_opacity": (0.0, 1.0),
    "size": (0.45, 2.2),
    "margin": (-0.35, 0.35),
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _hex(value: object, fallback: str = "#FFFFFF") -> str:
    raw = str(value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(c * 2 for c in raw)
    if len(raw) != 6 or any(c not in "0123456789abcdefABCDEF" for c in raw):
        raw = fallback.lstrip("#")
    return raw.upper()


def ass_colour(value: object, opacity: float = 1.0, fallback: str = "#FFFFFF") -> str:
    """`#RRGGBB` -> `&HAABBGGRR`. ASS alpha is inverted: 00 is opaque."""
    raw = _hex(value, fallback)
    red, green, blue = raw[0:2], raw[2:4], raw[4:6]
    alpha = int(round(_clamp(1.0 - float(opacity), 0.0, 1.0) * 255))
    return f"&H{alpha:02X}{blue}{green}{red}"


def resolve_style(value: object) -> dict:
    """Merge a template name or a partial style dict into a complete style."""
    if isinstance(value, str) or value is None:
        value = {"template": value or "bold"}
    if not isinstance(value, dict):
        value = {}

    name = str(value.get("template") or "bold").lower()
    if name not in CAPTION_TEMPLATES:
        name = "bold"

    style = dict(BASE_STYLE)
    style.update({k: v for k, v in CAPTION_TEMPLATES[name].items() if k != "label"})
    for key, raw in value.items():
        if key in BASE_STYLE and key != "template" and raw is not None:
            style[key] = raw
    style["template"] = name

    for key, (low, high) in _NUMERIC.items():
        try:
            style[key] = round(_clamp(float(style[key]), low, high), 3)
        except (TypeError, ValueError):
            style[key] = BASE_STYLE[key]

    for key, allowed in (("position", POSITIONS), ("animation", ANIMATIONS), ("box", BOXES)):
        if style[key] not in allowed:
            style[key] = BASE_STYLE[key]

    for key in ("bold", "italic", "uppercase", "karaoke"):
        style[key] = bool(style[key])
    for key in ("colour", "highlight", "outline_colour", "box_colour"):
        style[key] = f"#{_hex(style[key], BASE_STYLE[key])}"
    style["font"] = str(style.get("font") or "").strip()
    return style


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        centis, secs = 0, secs + 1
        if secs == 60:
            secs, minutes = 0, minutes + 1
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
        .strip()
    )


def _case(text: str, upper: bool) -> str:
    return text.upper() if upper else text


def _karaoke_text(caption: dict, upper: bool) -> str:
    """Wrap each word in a \\k tag so it lights up exactly when it is spoken."""
    words = caption.get("words") or []
    if not words:
        return _escape(_case(caption["text"], upper))

    parts = []
    cursor = float(caption["start"])
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        lead = max(0.0, start - cursor)
        if lead > 0.01:
            parts.append(f"{{\\k{int(round(lead * 100))}}}")
        duration = max(0.05, end - start)
        parts.append(f"{{\\k{int(round(duration * 100))}}}{_escape(_case(word['text'], upper))} ")
        cursor = end
    return "".join(parts).rstrip()


def _border(style: dict) -> tuple[int, float, float, str, str]:
    """(BorderStyle, Outline, Shadow, OutlineColour, BackColour) for a style.

    The subtlety is BorderStyle 3: libass fills the opaque box with the *outline*
    colour, not the back colour, and reads Outline as the box's padding. So a
    boxed caption has to hand its box colour to the outline slot, and the stroke
    it would otherwise have drawn is gone — which is exactly the flat CapCut
    look people are after when they turn the box on.
    """
    box = style["box"]
    outline_colour = ass_colour(style["outline_colour"], 1.0, "#000000")
    if box == "box":
        filled = ass_colour(style["box_colour"], style["box_opacity"], "#000000")
        return 3, max(2.0, style["outline"] * 2.0), 0.0, filled, filled
    if box == "shadow":
        return 1, style["outline"], max(1.0, style["shadow"]), outline_colour, \
            ass_colour(style["box_colour"], 0.55, "#000000")
    if box == "none":
        return 1, 0.0, 0.0, outline_colour, ass_colour("#000000", 0.0)
    return 1, style["outline"], style["shadow"], outline_colour, \
        ass_colour("#000000", 0.5)


def _alignment(position: str) -> int:
    return {"bottom": 2, "middle": 5, "top": 8}[position]


def _anchor(width: int, height: int, position: str, margin_v: int) -> tuple[int, int]:
    """Where a line of this alignment actually sits, for \\move animations."""
    if position == "top":
        return width // 2, margin_v
    if position == "middle":
        return width // 2, height // 2
    return width // 2, height - margin_v


def _caption_effect(style: dict, anchor: tuple[int, int], height: int) -> str:
    animation = style["animation"]
    if animation == "none":
        return ""
    if animation == "pop":
        return "{\\fad(70,90)\\fscx86\\fscy86\\t(0,190,\\fscx100\\fscy100)}"
    if animation == "rise":
        x, y = anchor
        lift = max(10, int(height * 0.035))
        return f"{{\\fad(110,90)\\move({x},{y + lift},{x},{y},0,320)}}"
    return "{\\fad(130,130)}"


# ── text overlays ─────────────────────────────────────────────────────────────

def _overlay_style_line(overlay: dict, index: int, width: int, height: int, font: str) -> str:
    size = max(12, int(height * _clamp(float(overlay.get("size", 0.08)), 0.02, 0.5)))
    opacity = _clamp(float(overlay.get("opacity", 1.0)), 0.05, 1.0)
    boxed = bool(overlay.get("box"))
    colour = ass_colour(overlay.get("colour"), opacity, "#FFFFFF")
    if boxed:
        # Same libass quirk as the captions: the opaque box is filled with the
        # outline colour, so that is where the box colour has to go.
        fill = ass_colour(
            overlay.get("box_colour"),
            _clamp(float(overlay.get("box_opacity", 0.6)), 0.0, 1.0) * opacity,
            "#000000",
        )
        outline_colour, back = fill, fill
    else:
        outline_colour = ass_colour(overlay.get("outline_colour"), opacity, "#000000")
        back = ass_colour(overlay.get("box_colour"), 0.55 * opacity, "#000000")
    border_style = 3 if boxed else 1
    # BorderStyle 3 reads Outline as box padding, so the two need very different
    # numbers to look the same weight.
    outline = round(size * 0.22, 2) if boxed else round(size * 0.09, 2)
    shadow = 0.0 if boxed else round(size * 0.04, 2)
    bold = -1 if overlay.get("bold", True) else 0
    italic = -1 if overlay.get("italic") else 0
    return (
        f"Style: Ov{index},{overlay.get('font') or font},{size},{colour},{colour},"
        f"{outline_colour},{back},{bold},{italic},0,0,100,100,0.4,0,"
        f"{border_style},{outline},{shadow},5,0,0,0,1"
    )


def _overlay_event(overlay: dict, index: int, width: int, height: int) -> str | None:
    text = _escape(str(overlay.get("text", "")))
    if not text:
        return None
    start = max(0.0, float(overlay.get("start", 0.0)))
    end = max(start + 0.2, float(overlay.get("end", start + 2.0)))

    x = int(width * _clamp(float(overlay.get("x", 0.5)), 0.0, 1.0))
    y = int(height * _clamp(float(overlay.get("y", 0.5)), 0.0, 1.0))
    shift_y = max(14, int(height * 0.06))
    shift_x = max(24, int(width * 0.08))

    animation = str(overlay.get("anim", "fade"))
    if animation == "none":
        effect = f"{{\\pos({x},{y})}}"
    elif animation == "pop":
        effect = f"{{\\pos({x},{y})\\fad(90,120)\\fscx70\\fscy70\\t(0,240,\\fscx100\\fscy100)}}"
    elif animation == "rise":
        effect = f"{{\\fad(140,160)\\move({x},{y + shift_y},{x},{y},0,420)}}"
    elif animation == "slide_left":
        effect = f"{{\\fad(140,160)\\move({x + shift_x},{y},{x},{y},0,420)}}"
    elif animation == "slide_right":
        effect = f"{{\\fad(140,160)\\move({x - shift_x},{y},{x},{y},0,420)}}"
    else:
        effect = f"{{\\pos({x},{y})\\fad(220,240)}}"

    rotate = float(overlay.get("rotate", 0.0) or 0.0)
    if abs(rotate) > 0.01:
        effect = effect[:-1] + f"\\frz{-round(_clamp(rotate, -180, 180), 2)}}}"

    layer = 2 + int(index)
    return f"Dialogue: {layer},{_ts(start)},{_ts(end)},Ov{index},,0,0,0,,{effect}{text}"


# ── the file ──────────────────────────────────────────────────────────────────

def build_ass(
    *,
    captions: list[dict],
    width: int,
    height: int,
    font: str,
    style: object = "bold",
    title_cards: list[dict] | None = None,
    overlays: list[dict] | None = None,
    include_captions: bool = True,
) -> str:
    resolved = resolve_style(style)
    budget = config.caption_budget(width, height)
    font_size = max(16, int(budget["font_size"] * resolved["size"]))
    caption_font = resolved["font"] or font

    landscape = width >= height
    base_margin = (0.09 if landscape else 0.20) + resolved["margin"]
    margin_v = int(height * _clamp(base_margin, 0.02, 0.6))
    margin_h = int(width * budget["margin"])
    title_size = int(font_size * 1.3)

    border_style, outline, shadow, outline_colour, back = _border(resolved)
    alignment = _alignment(resolved["position"])
    # \k paints the words already spoken in PrimaryColour and the ones still to
    # come in SecondaryColour, so a karaoke style swaps the two round.
    primary = ass_colour(resolved["highlight" if resolved["karaoke"] else "colour"])
    secondary = ass_colour(resolved["colour" if resolved["karaoke"] else "highlight"])
    bold = -1 if resolved["bold"] else 0
    italic = -1 if resolved["italic"] else 0

    overlay_styles: list[str] = []
    overlay_events: list[str] = []
    for i, overlay in enumerate(overlays or []):
        event = _overlay_event(overlay, i, width, height)
        if event is None:
            continue
        overlay_styles.append(_overlay_style_line(overlay, i, width, height, font))
        overlay_events.append(event)

    # WrapStyle 0 is the safety net: if a caption still overruns the usable
    # width, libass wraps it onto a second line instead of running off-frame.
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{caption_font},{font_size},{primary},{secondary},{outline_colour},{back},{bold},{italic},0,0,100,100,0.6,0,{border_style},{outline},{shadow},{alignment},{margin_h},{margin_h},{margin_v},1
Style: Title,{font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,-1,0,0,0,100,100,2,0,1,5,3,8,{margin_h},{margin_h},{int(height * 0.08)},1
{chr(10).join(overlay_styles)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines: list[str] = []

    for card in title_cards or []:
        text = _escape(card.get("text", ""))
        if not text:
            continue
        lines.append(
            f"Dialogue: 1,{_ts(card['start'])},{_ts(card['end'])},Title,,0,0,0,,"
            f"{{\\fad(300,300)}}{text}"
        )

    if include_captions:
        anchor = _anchor(width, height, resolved["position"], margin_v)
        effect = _caption_effect(resolved, anchor, height)
        for caption in captions:
            start = float(caption["start"])
            end = max(float(caption["end"]), start + 0.35)
            if resolved["karaoke"]:
                body = _karaoke_text(caption, resolved["uppercase"])
            else:
                body = _escape(_case(caption["text"], resolved["uppercase"]))
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{effect}{body}"
            )

    lines += overlay_events
    return header + "\n".join(lines) + "\n"


def write_ass(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _clock(seconds: float, millis_sep: str) -> str:
    seconds = max(0.0, seconds)
    hours, rem = divmod(int(seconds), 3600)
    minutes, secs = divmod(rem, 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis, secs = 0, secs + 1
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{millis_sep}{millis:03d}"


def build_srt(captions: list[dict]) -> str:
    """Plain SRT alongside the burned-in captions, for YouTube uploads."""
    blocks = []
    for i, caption in enumerate(captions, start=1):
        start = float(caption["start"])
        end = max(float(caption["end"]), start + 0.35)
        blocks.append(f"{i}\n{_clock(start, ',')} --> {_clock(end, ',')}\n"
                      f"{caption['text'].strip()}\n")
    return "\n".join(blocks)


def build_vtt(captions: list[dict]) -> str:
    """The same cues as WebVTT — what a browser player and YouTube both take.

    Worth having next to the SRT rather than instead of it: an editor wants SRT,
    a `<track>` element will only load VTT, and converting between them by hand
    is exactly the kind of chore this app exists to remove.
    """
    blocks = ["WEBVTT\n"]
    for i, caption in enumerate(captions, start=1):
        start = float(caption["start"])
        end = max(float(caption["end"]), start + 0.35)
        blocks.append(f"{i}\n{_clock(start, '.')} --> {_clock(end, '.')}\n"
                      f"{caption['text'].strip()}\n")
    return "\n".join(blocks)


def build_text(captions: list[dict], scenes: list[dict] | None = None) -> str:
    """The whole script as prose, with no timings — for a description or a blog.

    Built from the scenes when they are to hand, because that is the text as it
    was written and read: whole sentences, one paragraph per scene. Captions are
    the fallback, and they are chopped into three-word cues for the screen, so
    they are re-joined rather than listed — a transcript of one phrase per line
    is not a transcript anybody wants to read.
    """
    if scenes:
        paragraphs = [str(s.get("narration") or "").strip() for s in scenes]
        body = "\n\n".join(p for p in paragraphs if p)
        if body:
            return body + "\n"
    words = " ".join(str(c.get("text") or "").strip() for c in captions)
    return " ".join(words.split()) + "\n"


def parse_srt(text: str) -> list[dict]:
    """Read cues back out of an SRT. Used for videos rendered before captions
    were kept with the job, so their subtitles are still downloadable in any
    format rather than only the one shape that happens to be on disk."""
    def seconds(stamp: str) -> float:
        stamp = stamp.strip().replace(",", ".")
        parts = stamp.split(":")
        try:
            hours, minutes, rest = (parts + ["0", "0", "0"])[:3]
            return int(hours) * 3600 + int(minutes) * 60 + float(rest)
        except (TypeError, ValueError):
            return 0.0

    captions: list[dict] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        timing = next((ln for ln in lines if "-->" in ln), "")
        if not timing:
            continue
        start, _, end = timing.partition("-->")
        body = "\n".join(lines[lines.index(timing) + 1:]).strip()
        if body:
            captions.append({"start": seconds(start), "end": seconds(end), "text": body})
    return captions
