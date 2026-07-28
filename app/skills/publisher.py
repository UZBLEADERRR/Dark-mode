"""Skill 4 — Publisher.

Everything that surrounds the video file itself, written once per platform:
YouTube wants a searchable title and chapters, TikTok wants a short hook and
hashtags in the caption, Instagram wants something in between. One shared pack
would be wrong everywhere, so each gets its own.
"""

from __future__ import annotations

from .. import config
from .llm import call_json

SYSTEM = """You are the Publisher skill of an automated video studio.

You write the copy that ships alongside a video that has already been scripted:
one pack per platform, each written the way that platform actually reads.

YouTube
- Title under 70 characters, specific, promising exactly what the video delivers.
  No ALL CAPS, no clickbait the script does not pay off, at most one emoji.
- Description opens with two sentences that stand alone in search results, then a
  blank line, then the chapter list, then one short closing line.
- 12-18 tags, lowercase, no hash symbols, most specific first.
- Chapters start at 00:00 and use only timestamps from the supplied timeline.
  Group neighbouring scenes into 3-8 chapters with 2-5 word titles.

TikTok
- Caption under 150 characters. Lead with the hook, not a summary. Conversational.
- 4-6 hashtags, lowercase, no spaces inside a tag. Mix one broad tag with
  specific ones. Do not put the hashtags inside the caption text — list them
  separately.

Instagram
- Caption 2-4 short lines with a line break between them, ending on a question or
  an invitation to comment. Slightly warmer than TikTok, less formal than YouTube.
- 8-12 hashtags, lowercase, separate from the caption.

Rules for all three:
- Never invent a fact the script does not contain.
- The three captions must differ in wording. Do not paste the same sentence into
  all of them.
- `thumbnail_prompt` describes one high-contrast image with a single clear subject
  and negative space on one side. No text in the image.
- `music_mood` is a short search phrase for a royalty-free background track.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["youtube", "tiktok", "instagram", "thumbnail_prompt", "music_mood"],
    "properties": {
        "youtube": {
            "type": "object",
            "additionalProperties": False,
            "required": ["title", "description", "tags", "chapters"],
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
            },
        },
        "tiktok": {
            "type": "object",
            "additionalProperties": False,
            "required": ["caption", "hashtags"],
            "properties": {
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
        },
        "instagram": {
            "type": "object",
            "additionalProperties": False,
            "required": ["caption", "hashtags"],
            "properties": {
                "caption": {"type": "string"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
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


def _tags(values, limit: int) -> list[str]:
    out: list[str] = []
    for value in values or []:
        tag = str(value).strip().lstrip("#").lower().replace(" ", "")
        if tag and tag not in out:
            out.append(tag)
    return out[:limit]


def _empty(title: str, topic: str) -> dict:
    return {
        "title": title,
        "youtube": {"title": title, "description": topic, "tags": [], "chapters": []},
        "tiktok": {"caption": title, "hashtags": []},
        "instagram": {"caption": title, "hashtags": []},
        "thumbnail_prompt": "",
        "music_mood": "",
    }


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

COPY LANGUAGE
{lang_name}

SCENE TIMELINE
{beats}

Write the three publishing packs now, in {lang_name}."""

    try:
        data = await call_json(SYSTEM, user, SCHEMA, max_tokens=8000)
    except Exception:  # noqa: BLE001 - the video is finished; copy is a bonus
        return _empty(title, topic)

    youtube = data.get("youtube") or {}
    tiktok = data.get("tiktok") or {}
    instagram = data.get("instagram") or {}
    yt_title = (youtube.get("title") or title).strip()

    return {
        # Kept flat as well: the job list and the stage header read `title`.
        "title": yt_title,
        "youtube": {
            "title": yt_title,
            "description": (youtube.get("description") or "").strip(),
            "tags": _tags(youtube.get("tags"), 20),
            "chapters": youtube.get("chapters") or [],
        },
        "tiktok": {
            "caption": (tiktok.get("caption") or "").strip(),
            "hashtags": _tags(tiktok.get("hashtags"), 8),
        },
        "instagram": {
            "caption": (instagram.get("caption") or "").strip(),
            "hashtags": _tags(instagram.get("hashtags"), 15),
        },
        "thumbnail_prompt": (data.get("thumbnail_prompt") or "").strip(),
        "music_mood": (data.get("music_mood") or "").strip(),
    }
