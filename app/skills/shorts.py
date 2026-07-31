"""Finding the Shorts that are already inside a long video.

A long video is not a Short with the ends trimmed. A Short has to work for
somebody who arrived with no context and will leave in two seconds, which means
it needs its own opening — so the useful question is not "which part is best?"
but "which run of scenes stands up alone, starting from its first word?"

That is why this asks for scene *ranges* rather than timestamps. The app already
knows exactly how long every scene takes, because it recorded the voice-over, so
a range is a real duration rather than the model's guess at one — and the model
is free to think about where a thought starts and ends instead of doing
arithmetic it is bad at.

The suggestions are checked against the video afterwards: a range that runs off
the end, inverts, or overruns the length a Short may be is repaired or dropped
rather than offered. A suggestion you cannot cut is worse than one fewer.
"""

from __future__ import annotations

from typing import Any

from .llm import call_json

SYSTEM = """You choose the Shorts hidden inside a longer video.

You are given the video's scenes in order, each with its number, its spoken
narration and how many seconds it lasts.

What makes a Short worth cutting:
- It stands alone. Somebody who has not seen the long video must understand it
  from its own first sentence. A range that begins with "and that is why…"
  or "he then…" is not a Short, it is a fragment.
- It opens with something that stops the scroll — a claim, a number, a question,
  a turn. The first scene of the range is the hook, so choose the range for its
  beginning as much as its content.
- It finishes. A cliffhanger is fine; an unfinished sentence is not.
- It is one idea. Two ideas in forty seconds is a trailer, not a Short.

Rules you must follow:
- Use only the scene numbers you were given. Never invent one.
- `from_index` and `to_index` are inclusive, and `to_index` must not be smaller
  than `from_index`. A one-scene Short is allowed when that scene stands alone.
- Respect the length budget you are told. Adding scenes past it means the Short
  gets cut off mid-word, so a shorter, complete range always wins.
- Do not overlap the ranges. Each Short should be a different part of the video.
- Do not propose a Short you cannot justify from the narration in front of you.
  If the video only holds one good Short, return one.

For each Short:
- `title`: what you would actually put on the Short — under 60 characters, in the
  video's own language, no hashtags, no clickbait punctuation.
- `hook`: the first line of on-screen text, under 45 characters. It has to earn
  the second second.
- `why`: one sentence, in the video's language, saying what makes this stretch
  work on its own. Name the specific thing — the number, the turn, the claim.
  "Interesting part" is not a reason and will be rejected."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "shorts": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "from_index": {"type": "INTEGER"},
                    "to_index": {"type": "INTEGER"},
                    "title": {"type": "STRING"},
                    "hook": {"type": "STRING"},
                    "why": {"type": "STRING"},
                },
                "required": ["from_index", "to_index", "title", "hook", "why"],
                "propertyOrdering": ["from_index", "to_index", "title", "hook", "why"],
            },
        }
    },
    "required": ["shorts"],
}


def describe(scenes: list[dict[str, Any]]) -> str:
    """The video as the model needs to see it: numbered, timed, in its own words."""
    lines = []
    for scene in scenes:
        seconds = float(scene.get("audio_duration") or 0.0)
        text = str(scene.get("narration") or "").strip()
        lines.append(f"[{scene['index']}] ({seconds:.1f}s) {text}")
    return "\n".join(lines)


def span_seconds(scenes: list[dict[str, Any]], first: int, last: int) -> float:
    """How long that run of scenes actually lasts. Measured, never estimated."""
    return sum(float(s.get("audio_duration") or 0.0)
               for s in scenes if first <= int(s.get("index", -1)) <= last)


def tidy(raw: Any, scenes: list[dict[str, Any]], *, max_seconds: float,
         min_seconds: float = 5.0) -> list[dict[str, Any]]:
    """Keep the suggestions that can actually be cut, and say how long each is.

    Trimming from the end rather than the start is deliberate: the start is the
    hook the range was chosen for, and a Short that loses its opening loses the
    reason it was picked. Overruns lose their tail instead.
    """
    known = {int(s["index"]) for s in scenes}
    if not known:
        return []
    highest = max(known)

    out: list[dict[str, Any]] = []
    taken: set[int] = set()
    for item in (raw or {}).get("shorts", []) if isinstance(raw, dict) else []:
        try:
            first = int(item.get("from_index"))
            last = int(item.get("to_index"))
        except (TypeError, ValueError):
            continue
        if first not in known:
            continue
        last = max(first, min(last, highest))

        # Trim from the tail until it fits the budget.
        while last > first and span_seconds(scenes, first, last) > max_seconds:
            last -= 1
        length = span_seconds(scenes, first, last)
        if length <= 0 or length < min_seconds:
            continue
        # A single scene longer than the budget cannot be cut down by dropping
        # scenes, and half a sentence is not a Short.
        if length > max_seconds:
            continue
        if any(i in taken for i in range(first, last + 1)):
            continue

        out.append({
            "from_index": first,
            "to_index": last,
            "seconds": round(length, 1),
            "scene_count": last - first + 1,
            "title": str(item.get("title") or "").strip()[:100],
            "hook": str(item.get("hook") or "").strip()[:80],
            "why": str(item.get("why") or "").strip()[:300],
        })
        taken.update(range(first, last + 1))
    return out


async def suggest_shorts(
    *,
    scenes: list[dict[str, Any]],
    language: str,
    title: str = "",
    count: int = 3,
    max_seconds: float = 60.0,
) -> list[dict[str, Any]]:
    """Which parts of this video would work as Shorts, and why."""
    if not scenes:
        return []

    user = (
        f"Video title: {title or '(untitled)'}\n"
        f"Language: {language}\n"
        f"A Short here may be at most {max_seconds:.0f} seconds — the sum of the "
        f"scene lengths in your range must not exceed that.\n"
        f"Offer up to {count} Shorts, best first.\n\n"
        "Scenes:\n" + describe(scenes)
    )
    raw = await call_json(SYSTEM, user, SCHEMA, max_tokens=4000)
    return tidy(raw, scenes, max_seconds=max_seconds)[:count]
