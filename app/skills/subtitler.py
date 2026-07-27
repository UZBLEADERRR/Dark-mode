"""Skill 3 — Subtitler.

Claude decides *where* to break the narration into caption lines (a judgement
call: clauses, breath groups, keeping names intact). The code decides *when*
each line appears, by mapping those lines onto the word timings that came back
from the voice provider. Split that way, a hallucinated word can never desync
the subtitles.
"""

from __future__ import annotations

import re
import unicodedata

from .. import config
from .llm import call_json

SYSTEM = """You are the Subtitler skill of an automated video studio.

You break narration into on-screen caption lines. You are not rewriting anything:
the words, their order and their spelling must survive untouched. You only choose
where one line ends and the next begins.

Rules:
- Respect the character and word limits you are given. Shorter is better.
- Break on natural clause and breath boundaries — after commas, before conjunctions,
  before prepositional phrases. Never split a name, a number and its unit, or an
  article from its noun.
- Keep punctuation exactly where it was. Do not add or remove any word.
- The concatenation of your lines, in order, must equal the original narration exactly.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["scenes"],
    "properties": {
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "lines"],
                "properties": {
                    "index": {"type": "integer"},
                    "lines": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

def _norm(word: str) -> str:
    word = unicodedata.normalize("NFKC", word).casefold()
    return re.sub(r"[^\w']", "", word, flags=re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"\s+", text.strip()) if t]


def _chunk_deterministic(text: str, max_chars: int = 42, max_words: int = 7) -> list[str]:
    """Greedy fallback: fill lines up to the char/word budget, break on punctuation."""
    lines: list[str] = []
    current: list[str] = []
    for token in _tokens(text):
        candidate = current + [token]
        too_long = len(" ".join(candidate)) > max_chars or len(candidate) > max_words
        if too_long and current:
            lines.append(" ".join(current))
            current = [token]
        else:
            current = candidate
            if token.endswith((".", "!", "?", "…")) and len(current) >= 3:
                lines.append(" ".join(current))
                current = []
    if current:
        lines.append(" ".join(current))
    return lines or [text.strip()]


def _lines_match(lines: list[str], narration: str) -> bool:
    a = [_norm(t) for t in _tokens(" ".join(lines)) if _norm(t)]
    b = [_norm(t) for t in _tokens(narration) if _norm(t)]
    return a == b


async def _plan_lines(
    scenes: list[dict], language: str, max_chars: int, max_words: int
) -> dict[int, list[str]]:
    lang_name = config.LANGUAGES.get(language, language)
    blocks = "\n".join(f"[{s['index']}] {s['narration']}" for s in scenes)
    user = (
        f"Narration language: {lang_name}\n"
        f"Maximum {max_chars} characters and {max_words} words per line.\n\n"
        f"NARRATION BY SCENE\n{blocks}\n\n"
        f"Return caption lines for all {len(scenes)} scenes, keeping every word verbatim."
    )
    try:
        data = await call_json(SYSTEM, user, SCHEMA)
    except Exception:
        return {}

    planned: dict[int, list[str]] = {}
    for entry in data.get("scenes", []):
        try:
            idx = int(entry["index"])
        except (KeyError, TypeError, ValueError):
            continue
        lines = [str(line).strip() for line in entry.get("lines", []) if str(line).strip()]
        if lines:
            planned[idx] = lines
    return planned


def _attach_timings(lines: list[str], words: list[dict], start: float, end: float) -> list[dict]:
    """Walk the word-timing list in lockstep with the caption lines."""
    captions: list[dict] = []
    cursor = 0
    total_words = sum(len(_tokens(line)) for line in lines) or 1

    for position, line in enumerate(lines):
        count = len(_tokens(line))
        slice_ = words[cursor : cursor + count]
        cursor += count

        if slice_:
            line_start = float(slice_[0]["start"])
            line_end = float(slice_[-1]["end"])
            line_words = [
                {"text": w["text"], "start": float(w["start"]), "end": float(w["end"])}
                for w in slice_
            ]
        else:
            # No timings for this stretch — spread evenly across what's left.
            span = max(end - start, 0.1)
            done = sum(len(_tokens(l)) for l in lines[:position])
            line_start = start + span * (done / total_words)
            line_end = start + span * ((done + count) / total_words)
            line_words = []

        if line_end <= line_start:
            line_end = line_start + 0.4

        captions.append(
            {"text": line, "start": line_start, "end": line_end, "words": line_words}
        )

    # Close micro-gaps so captions do not flicker between lines.
    for i in range(len(captions) - 1):
        gap = captions[i + 1]["start"] - captions[i]["end"]
        if 0 < gap < 0.25:
            captions[i]["end"] = captions[i + 1]["start"]
    return captions


async def build_captions(
    *,
    scenes: list[dict],
    language: str,
    width: int = 1920,
    height: int = 1080,
    use_ai: bool = True,
) -> list[dict]:
    """Return a flat, time-ordered list of caption events for the whole video.

    Line length comes from the canvas: a Short gets short lines, a 16:9 video
    gets long ones, and `render.subtitles` sizes the font from the same budget.
    """
    budget = config.caption_budget(width, height)
    max_chars = int(budget["max_chars"])
    max_words = int(budget["max_words"])

    planned = (
        await _plan_lines(scenes, language, max_chars, max_words) if use_ai else {}
    )

    events: list[dict] = []
    for scene in scenes:
        narration = scene["narration"]
        lines = planned.get(scene["index"]) or []
        over_budget = any(len(line) > max_chars * 1.25 for line in lines)
        if not lines or over_budget or not _lines_match(lines, narration):
            lines = _chunk_deterministic(narration, max_chars, max_words)

        scene_start = float(scene.get("start", 0.0))
        scene_end = scene_start + float(scene.get("audio_duration", 0.0))
        words = [
            {
                "text": w["text"],
                "start": scene_start + float(w["start"]),
                "end": scene_start + float(w["end"]),
            }
            for w in scene.get("words", [])
        ]
        events.extend(_attach_timings(lines, words, scene_start, scene_end))

    events.sort(key=lambda e: e["start"])
    return events
