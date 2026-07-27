"""ASS subtitle generation.

ASS rather than SRT because it carries the styling — outline weight, position,
fades, per-word karaoke highlighting — inside the file, so libass burns exactly
what we designed instead of ffmpeg's defaults.
"""

from __future__ import annotations

from pathlib import Path

from .. import config

STYLES = {
    "bold": {
        "primary": "&H00FFFFFF",
        "secondary": "&H0000E5FF",
        "outline_colour": "&H00000000",
        "back": "&HA0000000",
        "border_style": 1,
        "outline": 4.5,
        "shadow": 2.0,
        "bold": -1,
        "size_scale": 1.0,
    },
    "clean": {
        "primary": "&H00FFFFFF",
        "secondary": "&H00D0D0D0",
        "outline_colour": "&HC0101010",
        "back": "&HC0101010",
        "border_style": 3,
        "outline": 1.2,
        "shadow": 0.0,
        "bold": 0,
        "size_scale": 0.88,
    },
    "karaoke": {
        "primary": "&H0055E0FF",   # colour a word turns as it is spoken
        "secondary": "&H00FFFFFF",  # colour before it is spoken
        "outline_colour": "&H00000000",
        "back": "&HA0000000",
        "border_style": 1,
        "outline": 4.5,
        "shadow": 2.0,
        "bold": -1,
        "size_scale": 1.0,
    },
}


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
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
        .strip()
    )


def _karaoke_text(caption: dict) -> str:
    """Wrap each word in a \\k tag so it lights up exactly when it is spoken."""
    words = caption.get("words") or []
    if not words:
        return _escape(caption["text"])

    parts = []
    cursor = float(caption["start"])
    for word in words:
        start = float(word["start"])
        end = float(word["end"])
        lead = max(0.0, start - cursor)
        if lead > 0.01:
            parts.append(f"{{\\k{int(round(lead * 100))}}}")
        duration = max(0.05, end - start)
        parts.append(f"{{\\k{int(round(duration * 100))}}}{_escape(word['text'])} ")
        cursor = end
    return "".join(parts).rstrip()


def build_ass(
    *,
    captions: list[dict],
    width: int,
    height: int,
    font: str,
    style: str = "bold",
    title_cards: list[dict] | None = None,
) -> str:
    preset = STYLES.get(style, STYLES["bold"])
    budget = config.caption_budget(width, height)
    font_size = max(18, int(budget["font_size"] * preset["size_scale"]))
    landscape = width >= height
    margin_v = int(height * (0.09 if landscape else 0.20))
    margin_h = int(width * budget["margin"])
    title_size = int(font_size * 1.3)

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
Style: Caption,{font},{font_size},{preset['primary']},{preset['secondary']},{preset['outline_colour']},{preset['back']},{preset['bold']},0,0,0,100,100,0.6,0,{preset['border_style']},{preset['outline']},{preset['shadow']},2,{margin_h},{margin_h},{margin_v},1
Style: Title,{font},{title_size},&H00FFFFFF,&H000000FF,&H00000000,&HB4000000,-1,0,0,0,100,100,2,0,1,5,3,8,{margin_h},{margin_h},{int(height * 0.08)},1

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

    for caption in captions:
        start = float(caption["start"])
        end = max(float(caption["end"]), start + 0.35)
        if style == "karaoke":
            body = _karaoke_text(caption)
            effect = "{\\fad(80,80)}"
        else:
            body = _escape(caption["text"])
            effect = "{\\fad(120,120)}"
        lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{effect}{body}")

    return header + "\n".join(lines) + "\n"


def write_ass(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def build_srt(captions: list[dict]) -> str:
    """Plain SRT alongside the burned-in captions, for YouTube uploads."""
    def srt_ts(seconds: float) -> str:
        seconds = max(0.0, seconds)
        hours, rem = divmod(int(seconds), 3600)
        minutes, secs = divmod(rem, 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis == 1000:
            millis, secs = 0, secs + 1
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    blocks = []
    for i, caption in enumerate(captions, start=1):
        start = float(caption["start"])
        end = max(float(caption["end"]), start + 0.35)
        blocks.append(f"{i}\n{srt_ts(start)} --> {srt_ts(end)}\n{caption['text'].strip()}\n")
    return "\n".join(blocks)
