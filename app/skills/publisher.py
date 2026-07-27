"""Skill 4 — Publisher.

Everything that surrounds the video file itself: the YouTube title and
description, tags, chapter markers, a thumbnail prompt, and the music mood the
render should reach for.
"""

from __future__ import annotations

from .. import config
from .llm import call_json

SYSTEM = """You are the Publisher skill of an automated video studio.

You write the YouTube metadata for a video that has already been scripted.

Rules:
- The title is under 70 characters, specific, and promises exactly what the video
  delivers. No ALL CAPS, no clickbait the script does not pay off, at most one emoji.
- The description opens with two sentences that stand on their own in search results,
  then a blank line, then the chapter list, then a short closing line.
- 12-18 tags, lowercase, no hashes, ordered from most to least specific.
- Chapters start at 00:00 and use the timestamps supplied. Titles are 2-5 words.
- The thumbnail prompt describes a single high-contrast image with one clear subject
  and a lot of negative space on one side. No text in the image.
- `music_mood` is a short search phrase for a royalty-free background track.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "title",
        "description",
        "tags",
        "chapters",
        "thumbnail_prompt",
        "music_mood",
    ],
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "chapters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["timestamp", "title"],
                "properties": {
                    "timestamp": {"type": "string"},
                    "title": {"type": "string"},
                },
            },
        },
        "thumbnail_prompt": {"type": "string"},
        "music_mood": {"type": "string"},
    },
}


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


async def build_publish_pack(
    *,
    topic: str,
    title: str,
    scenes: list[dict],
    language: str,
    duration: float,
) -> dict:
    lang_name = config.LANGUAGES.get(language, language)

    beats = "\n".join(
        f"{_timestamp(s.get('start', 0.0))} — {s['narration'][:140]}" for s in scenes
    )

    user = f"""TOPIC
{topic}

WORKING TITLE
{title}

TOTAL LENGTH
{_timestamp(duration)}

METADATA LANGUAGE
{lang_name}

SCENE TIMELINE
{beats}

Write the publishing pack now, in {lang_name}. Build the chapters from the timeline
above — group neighbouring scenes into 3-8 chapters, and use only timestamps that
appear in the timeline."""

    try:
        data = await call_json(SYSTEM, user, SCHEMA, max_tokens=8000)
    except Exception:
        return {
            "title": title,
            "description": topic,
            "tags": [],
            "chapters": [{"timestamp": "00:00", "title": "Start"}],
            "thumbnail_prompt": "",
            "music_mood": "",
        }

    return {
        "title": (data.get("title") or title).strip(),
        "description": (data.get("description") or "").strip(),
        "tags": [str(t).strip().lower() for t in data.get("tags", []) if str(t).strip()][:20],
        "chapters": data.get("chapters", []),
        "thumbnail_prompt": (data.get("thumbnail_prompt") or "").strip(),
        "music_mood": (data.get("music_mood") or "").strip(),
    }
