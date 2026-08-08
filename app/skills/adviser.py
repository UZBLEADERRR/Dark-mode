"""How long this subject should run, and whether it is a Short or a long video.

Length and shape are the two decisions a person has to make before anything can
be made, and they are the two the app used to leave entirely to them — a number
box with a default in it, chosen before knowing anything about the subject. A
title is enough to say something useful about both: some subjects have four
minutes in them and some have forty seconds, and picking the wrong one is a
video nobody watches to the end.
"""
from __future__ import annotations

from typing import Any

from . import llm
from .strategist import SHAPES, describe_profiles

SYSTEM = """You advise on the length and shape of one video, before it is made.

You are given a title or a subject. Say how long it should run and whether it
belongs on Shorts/Reels/TikTok or as a long video, and say why in one sentence
the person can disagree with.

Judge the subject, not the average video. Ask yourself how much a viewer has to
be told before the thing is worth having watched:

  - a single fact, one reveal, one joke, one before-and-after -> Shorts
  - a story with a turn in it, a how-to with steps, a comparison, anything that
    needs context before the payoff -> long

`shorts` runs 20-60 seconds and is 9:16. `long` runs 3-15 minutes and is 16:9.
Stay inside those bounds; they are what the platforms actually reward.

`seconds` is your single recommendation, not the middle of the range. `low` and
`high` bracket it — the span where the video still works. Keep the bracket tight
enough to be a decision: 40-70 is advice, 20-900 is not.

`both` is true only when the subject genuinely works either way, and then
`other_seconds` is what it would run as the other shape. Most subjects are not
like this; saying so falsely is worse than choosing.

`why` is one sentence, in the same language as the title (Uzbek in latin script
when it is ambiguous). Say what about THIS subject decides it — "there is one
reveal and no setup needed", not "shorts perform well". No markdown.

`title_note` is optional: one short remark if the title itself is the problem —
too vague to film, or promising more than one video's worth. Empty otherwise."""

SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": ["shorts", "long"]},
        "seconds": {"type": "integer"},
        "low": {"type": "integer"},
        "high": {"type": "integer"},
        "both": {"type": "boolean"},
        "other_seconds": {"type": "integer"},
        "why": {"type": "string"},
        "title_note": {"type": "string"},
    },
    "required": ["shape", "seconds", "low", "high", "both", "why"],
}


async def advise_length(topic: str, *, profiles: list[dict[str, Any]] | None = None,
                        language: str = "") -> dict[str, Any]:
    """What shape and length this subject wants. Never raises for a bad answer."""
    said = [f"Title or subject: {topic.strip()[:500]}"]
    if language:
        said.append(f"The video will be narrated in: {language}")
    # The same channels the assistant reads. A subject lands differently on a
    # channel whose audience already knows the background than on one whose
    # does not, and the length is exactly where that difference shows.
    if profiles:
        said.append("\nTheir channels:\n" + describe_profiles(profiles))

    out = await llm.call_json(SYSTEM, "\n".join(said), SCHEMA, max_tokens=1200)
    return _settle(out if isinstance(out, dict) else {}, topic)


def _settle(out: dict[str, Any], topic: str) -> dict[str, Any]:
    """Bound the answer to lengths the app can actually make.

    A model that recommends eleven seconds or two hours has not given advice
    worth acting on, and the composer would refuse the number anyway — so the
    bracket is applied here rather than left to be discovered at submit time.
    """
    shape = out.get("shape") if out.get("shape") in SHAPES else "shorts"
    low_bound, high_bound = SHAPES[shape]["seconds"]

    def bound(value: object, fallback: int) -> int:
        try:
            return max(low_bound, min(high_bound, int(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return fallback

    seconds = bound(out.get("seconds"), low_bound)
    low = bound(out.get("low"), low_bound)
    high = bound(out.get("high"), high_bound)
    # A bracket that does not contain the recommendation is not a bracket.
    low, high = min(low, seconds), max(high, seconds)

    other = SHAPES["long" if shape == "shorts" else "shorts"]
    both = bool(out.get("both"))
    other_seconds = 0
    if both:
        try:
            other_seconds = int(out.get("other_seconds") or 0)
        except (TypeError, ValueError):
            other_seconds = 0
        other_seconds = max(other["seconds"][0],
                            min(other["seconds"][1], other_seconds or other["seconds"][0]))

    return {
        "topic": topic.strip()[:500],
        "shape": shape,
        "label": SHAPES[shape]["label"],
        "video_format": SHAPES[shape]["format"],
        "seconds": seconds,
        "low": low,
        "high": high,
        "both": both,
        "other_shape": "long" if shape == "shorts" else "shorts",
        "other_label": other["label"],
        "other_format": other["format"],
        "other_seconds": other_seconds,
        "why": str(out.get("why") or "").strip()[:400],
        "title_note": str(out.get("title_note") or "").strip()[:200],
    }
