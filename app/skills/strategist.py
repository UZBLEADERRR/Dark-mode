"""The one skill you talk to.

Everything else in `skills/` is given a job and does it. This one is given a
conversation and works out what job to do — which is a different shape of
problem, so it gets a different shape of answer:

    {"reply": …, "ideas": [...], "asks": [...], "create": {...} | null}

`reply` is what to say. `ideas` are things worth making, for tapping rather than
typing. `asks` are the details still missing, offered as choices. `create` is the
complete video request, and it is only ever non-null once nothing is missing —
that is the whole contract: the model does not get to start a render it has not
finished asking about.
"""

from __future__ import annotations

from typing import Any

from .. import config
from . import llm

# What a channel screenshot is read for. Deliberately not "describe the image":
# the picture is a means to an end, and the end is knowing what this account is
# for and who watches it.
READ_SYSTEM = """You read a screenshot of somebody's own social media profile and
report what an experienced content strategist would notice about it.

Report only what is visible or a fair inference from what is visible. Never
invent follower counts, view counts or names. If something is not legible, leave
that field empty rather than guessing — an empty field is far more useful than a
plausible fiction, because everything downstream is built on this.

Be specific where the screenshot lets you be. `niche` is not "content" — it is
the actual subject: "beginner Python tutorials", "Uzbek street food reviews",
"amateur football analysis". `pillars` are the recurring kinds of post you can
actually see in the grid or the video titles, named one by one. `style` is how
the posts are made: talking head or voice-over, subtitles burnt in or not, face
on camera or not, fast cuts or slow, thumbnail style, how the titles are
written.

`language` is the language of the posts themselves, not of the interface.

Write `summary` as three or four plain sentences somebody would find useful
months later. No bullet points, no headings."""

READ_SCHEMA = {
    "type": "object",
    "properties": {
        "handle": {"type": "string", "description": "@name, or empty if unreadable"},
        "platform": {"type": "string", "enum": ["instagram", "youtube", "tiktok", "other"]},
        "niche": {"type": "string", "description": "the actual subject, specifically"},
        "audience": {"type": "string", "description": "who watches: who they are, roughly how old, where"},
        "language": {"type": "string", "description": "two-letter code of the posts' language"},
        "pillars": {"type": "string",
                    "description": "the recurring kinds of post visible here, named, comma separated"},
        "style": {"type": "string",
                  "description": "how the posts are made: format, pacing, subtitles, on-camera or not"},
        "summary": {"type": "string"},
    },
    "required": ["handle", "platform", "niche", "audience", "language", "pillars",
                 "style", "summary"],
}


PLATFORMS = {
    "youtube": "YouTube",
    "instagram": "Instagram",
    "tiktok": "TikTok",
}

# The two shapes a video can be, and what each one actually means in seconds. The
# model is told these rather than left to invent a length, because "short" means
# something specific on every one of these platforms.
SHAPES = {
    "shorts": {"label": "Shorts / Reels / TikTok", "format": "9:16", "seconds": (20, 60)},
    "long": {"label": "Uzun video", "format": "16:9", "seconds": (180, 900)},
}


CHAT_SYSTEM = """You are Sarideo's studio assistant. You talk to one person about
their own social media channels and you make videos for them.

You have three jobs, in this order:

1. ANSWER. Reply in the same language the person is writing in. Default to Uzbek
   (latin script) when it is ambiguous. Be brief — this is a phone screen. Never
   use markdown formatting, headings or bullet characters; write in sentences.

2. OFFER IDEAS when asked for them, or when the person names a channel and a
   format. Put them in `ideas`, not in `reply` — the app draws them as cards the
   person can tap. Six at most.

   These have to be this channel's ideas. The test each one must pass: if it
   would suit any account on the platform equally well, it is not an idea, it is
   filler — delete it and think again. Every idea carries a `fit` field naming
   the specific thing about THIS channel it is built on: a content pillar it
   extends, a gap in what they already post, the audience it is aimed at. If you
   cannot write that sentence honestly, the idea is not grounded and does not go
   in the list.

   Concretely, this rules out:
     - a famous name the channel has never mentioned, as a topic in itself
     - "top 5 …" of something the channel does not cover
     - motivational or generic-advice content on a channel that is not about that
     - anything you would have suggested before reading their profile

   `title` must be specific enough to film: "Three Uzbek foods foreigners always
   get wrong" is a title, "food content" is not. Write titles and hooks in the
   channel's own language, in the register that channel uses — not translated
   English phrasing.

   If you have not been shown any channel, say so plainly and ask what the
   channel is about before offering anything. Guessed ideas waste their time.

3. MAKE THE VIDEO. When the person has chosen what to make, your goal is a
   complete `create` object. Anything you still need goes in `asks` — never in
   `reply` as a question, because `asks` are tappable and a question is not.

WHAT YOU MUST KNOW BEFORE `create`:
  - what the video is about (`topic`) — required, be specific
  - shorts or long, which fixes `video_format`: 9:16 for shorts, 16:9 for long
  - `target_seconds` — how long
  - `language` — two-letter code
  - whether any of their characters appear in it (`hero_ids`), when they have any
  - whether the characters should move about like a cartoon (`animate_actors`)

Assume nothing about length or shape: those are the two things people care most
about and the two you cannot guess. You may infer `language` from the channel or
from how the person writes to you, and you may leave `hero_ids` empty when they
have no characters. One round of `asks` covering everything is much better than
five rounds of one question.

Set `create` to null on every turn until you have it all. When you do set it,
`reply` should say in one sentence what you are about to make, not ask anything.

`action` is optional and is what happens on screen in the person's own words —
fill it only if they described it. `art_style` and `tone` you may choose to suit
the channel.

NEVER put a question in `reply` and also set `create`. Never claim a video is
being made unless you set `create`."""

CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "hook": {"type": "string", "description": "the first line the viewer hears"},
                    "why": {"type": "string", "description": "one short sentence on why it works here"},
                    "fit": {"type": "string",
                            "description": "the specific thing about THIS channel this is built on — "
                                           "a pillar it extends, a gap it fills, the audience it targets"},
                    "shape": {"type": "string", "enum": ["shorts", "long"]},
                    "seconds": {"type": "integer"},
                    "topic": {"type": "string", "description": "the brief a video generator could work from"},
                },
                "required": ["title", "hook", "why", "fit", "shape", "seconds", "topic"],
            },
        },
        "asks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "options"],
            },
        },
        "create": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "action": {"type": "string"},
                "video_format": {"type": "string", "enum": ["9:16", "16:9", "1:1", "4:5"]},
                "target_seconds": {"type": "integer"},
                "language": {"type": "string"},
                "tone": {"type": "string"},
                "art_style": {"type": "string"},
                "hero_ids": {"type": "array", "items": {"type": "string"}},
                "animate_actors": {"type": "boolean"},
            },
            "required": ["topic", "video_format", "target_seconds", "language"],
        },
    },
    "required": ["reply"],
}


async def read_profile(data: bytes, mime: str, platform: str = "") -> dict[str, Any]:
    """Look at a channel screenshot once and write down what is worth keeping."""
    hint = f"The person says this is their {PLATFORMS.get(platform, platform)} profile." \
        if platform else "Work out which platform this is from the screenshot."
    try:
        out = await llm.call_json(
            READ_SYSTEM, hint, READ_SCHEMA, max_tokens=1500, images=[(data, mime)])
    except llm.LLMError:
        # A profile that could not be read is still a profile worth keeping — the
        # person uploaded it, and they can say what it is themselves.
        return _blank(platform)
    if not isinstance(out, dict):
        return _blank(platform)
    read = {**_blank(platform), **{k: str(v or "").strip()
                                   for k, v in out.items() if isinstance(v, (str, int, float))}}
    if platform:
        read["platform"] = platform
    return read


def _blank(platform: str = "") -> dict[str, Any]:
    return {"handle": "", "platform": platform or "other", "niche": "", "audience": "",
            "language": "", "pillars": "", "style": "", "summary": ""}


def describe_profiles(profiles: list[dict[str, Any]]) -> str:
    """The channels, as the assistant knows them. Text, not pictures.

    Reading the screenshots was paid for when they were uploaded. Sending them
    again with every message would pay for it on every turn and tell the model
    nothing it was not already told.

    Everything that was read goes in, not just the prose summary. The summary
    alone was too thin to hold an answer to: a model given nothing specific has
    nothing particular to be faithful to, and answers with something that would
    suit any channel — which is exactly the complaint.
    """
    if not profiles:
        return ("The person has not shown you any of their channels yet. Do not "
                "guess what their channel is about — ask them.")
    blocks = []
    for p in profiles:
        where = PLATFORMS.get(p.get("platform"), p.get("platform") or "?")
        head = f"{where} {p.get('handle') or '(nomsiz)'}"
        facts = [
            ("Subject", p.get("niche")),
            ("Audience", p.get("audience")),
            ("Language of the posts", p.get("language")),
            ("What they already post", p.get("pillars")),
            ("How the posts are made", p.get("style")),
            ("Read from the screenshot", p.get("summary")),
        ]
        detail = "\n".join(f"  {label}: {value}" for label, value in facts if value)
        blocks.append(f"{head}\n{detail}" if detail else
                      f"{head}\n  Nothing legible was read — ask what it is about.")
    return ("The person's own channels. Every idea you offer must be traceable to "
            "something in here:\n" + "\n".join(blocks))


def describe_cast(heroes: list[dict[str, Any]]) -> str:
    if not heroes:
        return ("They have no saved characters. Leave hero_ids empty and do not "
                "offer to put characters in the video.")
    lines = [f"- {h['id']}: {h['name']}"
             + (f" — {h['description']}" if h.get("description") else "")
             + (" (has its own voice)" if h.get("voice_id") else "")
             for h in heroes]
    return ("Their saved characters, usable as hero_ids exactly as written:\n"
            + "\n".join(lines))


def _turns(history: list[dict[str, Any]], keep: int = 24) -> str:
    """The conversation so far, oldest first, as plain text."""
    out = []
    for turn in history[-keep:]:
        who = "PERSON" if turn.get("role") == "user" else "YOU"
        text = (turn.get("text") or "").strip()
        if text:
            out.append(f"{who}: {text}")
    return "\n".join(out)


async def chat(
    *,
    message: str,
    history: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    heroes: list[dict[str, Any]],
    languages: list[str] | None = None,
) -> dict[str, Any]:
    """One turn of the conversation. Never raises for lack of an answer."""
    shapes = "\n".join(
        f"- {key}: {v['label']}, {v['format']}, {v['seconds'][0]}–{v['seconds'][1]} seconds"
        for key, v in SHAPES.items())
    user = "\n\n".join(filter(None, [
        describe_profiles(profiles),
        describe_cast(heroes),
        f"Video shapes available:\n{shapes}",
        f"Languages the app can narrate in: {', '.join(languages or ['uz', 'en', 'ru'])}.",
        f"The conversation so far:\n{_turns(history)}" if history else "",
        f"PERSON: {message}",
    ]))

    out = await llm.call_json(CHAT_SYSTEM, user, CHAT_SCHEMA, max_tokens=6000)
    if not isinstance(out, dict):
        raise llm.LLMError("The assistant did not answer in the expected shape.")
    return normalise(out)


def normalise(out: dict[str, Any]) -> dict[str, Any]:
    """Make the model's answer safe to act on.

    Two things matter here. A `create` missing anything required is not a
    request, it is a half-formed thought — it becomes an `ask` instead, so the
    conversation carries on rather than a broken job being started. And a length
    is clamped to what the renderer will accept, because a model that says 3600
    seconds means "long", not "an hour".
    """
    ideas = []
    for idea in (out.get("ideas") or [])[:6]:
        if not isinstance(idea, dict) or not (idea.get("title") or "").strip():
            continue
        shape = idea.get("shape") if idea.get("shape") in SHAPES else "shorts"
        low, high = SHAPES[shape]["seconds"]
        ideas.append({
            "title": str(idea["title"]).strip()[:120],
            "hook": str(idea.get("hook") or "").strip()[:300],
            "why": str(idea.get("why") or "").strip()[:300],
            # What this idea leans on. Shown, because an idea whose grounding is
            # visible is one you can disagree with — and a blank one is a
            # generic idea admitting it.
            "fit": str(idea.get("fit") or "").strip()[:300],
            "shape": shape,
            "seconds": max(low, min(high, int(idea.get("seconds") or low))),
            "topic": str(idea.get("topic") or idea["title"]).strip()[:500],
        })

    asks = []
    for ask in (out.get("asks") or [])[:4]:
        if not isinstance(ask, dict) or not (ask.get("question") or "").strip():
            continue
        options = [str(o).strip()[:80] for o in (ask.get("options") or []) if str(o).strip()]
        asks.append({"question": str(ask["question"]).strip()[:200], "options": options[:6]})

    create = out.get("create")
    if isinstance(create, dict) and (create.get("topic") or "").strip():
        fmt = create.get("video_format")
        if fmt not in config.FORMATS:
            fmt = "9:16"
        seconds = int(create.get("target_seconds") or 0)
        if seconds <= 0:
            seconds = 45 if fmt == "9:16" else 180
        create = {
            "topic": str(create["topic"]).strip()[:500],
            "action": str(create.get("action") or "").strip()[:4000] or None,
            "video_format": fmt,
            "target_seconds": max(20, min(1800, seconds)),
            "language": (str(create.get("language") or "uz").strip()[:5] or "uz"),
            "tone": str(create.get("tone") or "").strip()[:200] or None,
            "art_style": str(create.get("art_style") or "").strip()[:400] or None,
            "hero_ids": [str(h) for h in (create.get("hero_ids") or [])][:6],
            "animate_actors": bool(create.get("animate_actors")),
        }
    else:
        create = None

    reply = str(out.get("reply") or "").strip()
    if not reply:
        reply = "Qanday video kerak?" if not ideas else "Mana bir nechta g‘oya:"
    return {"reply": reply[:2000], "ideas": ideas, "asks": asks, "create": create}
