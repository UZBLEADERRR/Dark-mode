"""Skill 1 — Director.

Takes the topic the user typed plus the format/duration knobs and returns a
scene-by-scene script: what the narrator says, which heroes appear, and how the
camera should move over the still image for that beat.
"""

from __future__ import annotations

import math

from .. import config
from .llm import call_json

def describe_cast(heroes: list[dict]) -> str:
    """The cast list the model is given — and, crucially, who can speak.

    A character only gets its own line when somebody has actually chosen a voice
    for it. Saying so here rather than filtering afterwards is what stops the
    model writing dialogue for a character that would then be read by the
    narrator anyway, in a voice that belongs to nobody in the story.
    """
    if not heroes:
        return "(no recurring characters — build the visuals from places, objects and action)"
    lines = []
    for h in heroes:
        line = f"- id: {h['id']} | name: {h['name']}"
        if h.get("description"):
            line += f" | {h['description']}"
        line += " | HAS ITS OWN VOICE — may speak" if h.get("voice_id") else " | no voice — narrator only"
        lines.append(line)
    return "\n".join(lines)


def pick_speaker(raw: object, heroes: list[dict]) -> str:
    """Accept a cast line only from a character that can actually say it."""
    wanted = str(raw or "").strip()
    if not wanted:
        return ""
    return wanted if any(h["id"] == wanted and h.get("voice_id") for h in heroes) else ""


MOTIONS = [
    "zoom_in",
    "zoom_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "zoom_in_pan_right",
    "zoom_out_pan_left",
]

SYSTEM = """You are the Director skill of an automated YouTube video studio.

You turn a single topic line into a finished narration script broken into scenes.
Each scene becomes ONE generated still image with a slow camera move over it, plus
the narrator audio for that beat. The viewer never sees a cut without a reason.

Rules that matter:
- Write narration the way a person actually speaks: contractions, short sentences,
  concrete nouns. No bullet points, no headings, no stage directions in narration.
- Every scene's narration must be speakable in roughly the seconds budgeted for it.
  Aim for about 2.4 words per second in English and adjust for the target language.
- Scene 1 is a hook. Never open with "In this video we will". Open with the most
  surprising, concrete thing you have.
- The last scene closes the loop and invites the next click without begging.
- `visual` describes what is literally on screen for that beat — a place, a person,
  an object, an action. It must be something a still image can show. Never write
  "text on screen saying X" as the visual.
- `hero_ids` lists which of the supplied characters physically appear in that shot.
  Leave it empty for landscapes, objects, or abstract beats. Only use ids you were given.
- `speaker` is who says that line. Leave it empty and the narrator reads it, which is
  right for most videos. Set it to a character id ONLY when that character has its own
  voice (the cast list says so) and the line is that character speaking for itself —
  then write the narration as the words they actually say, not as narration about them.
  Never put a character's id there if the cast list does not mark it as having a voice.
- `motion` is the camera move. Prefer zoom_in for reveals and tension, zoom_out for
  context and endings, pans for landscapes and for following an action.
- `on_screen_text` is an optional 1-4 word title card for that scene. Use it sparingly,
  on at most a quarter of the scenes, and leave it empty otherwise.
- Vary the motion between neighbouring scenes; never use the same one three times in a row.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "hook", "scenes"],
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "visual", "motion", "hero_ids", "speaker",
                             "on_screen_text"],
                "properties": {
                    "narration": {"type": "string"},
                    "visual": {"type": "string"},
                    "motion": {"type": "string", "enum": MOTIONS},
                    "hero_ids": {"type": "array", "items": {"type": "string"}},
                    "speaker": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                },
            },
        },
    },
}


async def direct_script(
    *,
    topic: str,
    target_seconds: int,
    language: str,
    tone: str,
    video_format: str,
    heroes: list[dict],
    action: str = "",
    cartoon: bool = False,
) -> dict:
    scene_count = max(3, min(config.MAX_SCENES, round(target_seconds / config.SECONDS_PER_SCENE)))
    per_scene = target_seconds / scene_count
    lang_name = config.LANGUAGES.get(language, language)
    fmt = config.FORMATS.get(video_format, config.FORMATS["16:9"])

    cast = describe_cast(heroes)

    action_block = f"""
WHAT MUST HAPPEN (the user's own words — follow this, do not replace it)
{action.strip()}
""" if action.strip() else ""

    # A cartoon is characters speaking. Left to itself the Director writes
    # narration *about* them — "Tarzan hesitated" — and every line comes back in
    # the narrator's voice, which is a slideshow with a commentary.
    if cartoon:
        talkers = [h for h in heroes if h.get("voice_id")]
        if talkers:
            names = ", ".join(h["name"] for h in talkers)
            action_block += f"""
THIS IS A CARTOON
Write it as a story the characters act out, not as a documentary about them.
{names} have voices of their own. Give most of the lines to them, as dialogue:
set `speaker` to that character's id and write the narration field as the words
they actually say — "Bu yerda nimadir bor" — never as narration about them
("Tarzan hesitated"). Keep the narrator for scene-setting only, a few lines at
most, and none at all if the story carries itself.
"""
        else:
            action_block += """
THIS IS A CARTOON
Write it as a story with action in it rather than a description of one. No
character has been given a voice, so the narrator reads every line — keep the
narration close to what is happening on screen.
"""

    user = f"""TOPIC
{topic}

SPEC
- Target length: about {target_seconds} seconds total
- Scenes: exactly {scene_count}
- Time budget per scene: about {per_scene:.1f} seconds of narration
- Narration language: {lang_name}
- Tone: {tone}
- Aspect ratio: {fmt['aspect']} ({fmt['label']})

CAST (recurring characters the studio already has reference photos for)
{cast}
{action_block}
Write the full script now. Write ALL narration and on-screen text in {lang_name}.
Write the `visual` field in English regardless of narration language — it feeds an
image generator. Return exactly {scene_count} scenes."""

    data = await call_json(SYSTEM, user, SCHEMA)

    known_ids = {h["id"] for h in heroes}
    scenes = []
    for i, raw in enumerate(data.get("scenes", [])):
        narration = (raw.get("narration") or "").strip()
        if not narration:
            continue
        motion = raw.get("motion") if raw.get("motion") in MOTIONS else MOTIONS[i % len(MOTIONS)]
        scenes.append(
            {
                "index": len(scenes),
                "narration": narration,
                "visual": (raw.get("visual") or narration)[:600],
                "motion": motion,
                "hero_ids": [h for h in (raw.get("hero_ids") or []) if h in known_ids],
                "speaker": pick_speaker(raw.get("speaker"), heroes),
                "on_screen_text": (raw.get("on_screen_text") or "").strip()[:60],
            }
        )

    if not scenes:
        raise ValueError("The Director skill returned no usable scenes.")

    return {
        "title": (data.get("title") or topic).strip(),
        "hook": (data.get("hook") or "").strip(),
        "scenes": scenes,
        "planned_scene_count": scene_count,
        "seconds_per_scene": round(per_scene, 2),
    }


SEGMENT_SYSTEM = """You are the Director skill working from narration that already exists.

The user recorded or supplied the voice-over; you cannot change a single word of it.
Your job is to cut that narration into scenes and decide what the viewer sees during
each one.

Rules:
- Cut on meaning, not on a clock. A scene ends where the idea ends.
- Copy the narration text verbatim into each scene. Concatenating your scenes in order
  must reproduce the transcript exactly, with no words added, removed or respelled.
- `visual` describes what a single still image shows for that beat, in English.
- `hero_ids` lists which supplied characters appear in the shot; empty is fine.
- `speaker` is who says the line: empty for the narrator, or a character id when the
  cast list marks that character as having its own voice and the line is that
  character speaking. Never invent an id.
- Vary `motion` between neighbouring scenes.
- `on_screen_text` is optional and rare — a 1-4 word title card.
"""

SEGMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "scenes"],
    "properties": {
        "title": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["narration", "visual", "motion", "hero_ids", "speaker",
                             "on_screen_text"],
                "properties": {
                    "narration": {"type": "string"},
                    "visual": {"type": "string"},
                    "motion": {"type": "string", "enum": MOTIONS},
                    "hero_ids": {"type": "array", "items": {"type": "string"}},
                    "speaker": {"type": "string"},
                    "on_screen_text": {"type": "string"},
                },
            },
        },
    },
}


async def segment_existing_narration(
    *,
    topic: str,
    transcript: str,
    duration: float,
    language: str,
    video_format: str,
    heroes: list[dict],
) -> dict:
    """Storyboard a voice-over the user already supplied."""
    scene_count = max(3, min(config.MAX_SCENES, round(duration / config.SECONDS_PER_SCENE)))
    fmt = config.FORMATS.get(video_format, config.FORMATS["16:9"])

    cast = describe_cast(heroes)

    user = f"""TOPIC / CONTEXT
{topic}

AUDIO LENGTH
{duration:.1f} seconds — aim for about {scene_count} scenes.

ASPECT RATIO
{fmt['aspect']} ({fmt['label']})

CAST
{cast}

TRANSCRIPT (verbatim — do not alter)
{transcript}

Cut this into scenes now."""

    data = await call_json(SEGMENT_SYSTEM, user, SEGMENT_SCHEMA)

    known_ids = {h["id"] for h in heroes}
    scenes = []
    for i, raw in enumerate(data.get("scenes", [])):
        narration = (raw.get("narration") or "").strip()
        if not narration:
            continue
        motion = raw.get("motion") if raw.get("motion") in MOTIONS else MOTIONS[i % len(MOTIONS)]
        scenes.append(
            {
                "index": len(scenes),
                "narration": narration,
                "visual": (raw.get("visual") or narration)[:600],
                "motion": motion,
                "hero_ids": [h for h in (raw.get("hero_ids") or []) if h in known_ids],
                "speaker": pick_speaker(raw.get("speaker"), heroes),
                "on_screen_text": (raw.get("on_screen_text") or "").strip()[:60],
            }
        )

    if not scenes:
        raise ValueError("The Director skill could not segment this narration.")

    return {"title": (data.get("title") or topic).strip(), "scenes": scenes}


async def segment_written_script(
    *,
    topic: str,
    script: str,
    language: str,
    video_format: str,
    heroes: list[dict],
) -> dict:
    """Storyboard a script the user wrote, using their words verbatim."""
    words = len([w for w in script.split() if w])
    # ~2.4 words a second is a normal narration pace; it only sets scene count.
    estimated = max(10.0, words / 2.4)
    scene_count = max(2, min(config.MAX_SCENES, round(estimated / config.SECONDS_PER_SCENE)))
    fmt = config.FORMATS.get(video_format, config.FORMATS["16:9"])
    lang_name = config.LANGUAGES.get(language, language)

    cast = describe_cast(heroes)

    user = f"""CONTEXT
{topic or "(none given — the script speaks for itself)"}

SCRIPT LANGUAGE
{lang_name}

ASPECT RATIO
{fmt['aspect']} ({fmt['label']})

CAST
{cast}

SCRIPT (verbatim — every word must survive, in this order)
{script}

Cut this into roughly {scene_count} scenes. Write each `visual` in English even
though the narration is in {lang_name}."""

    data = await call_json(SEGMENT_SYSTEM, user, SEGMENT_SCHEMA)

    known_ids = {h["id"] for h in heroes}
    scenes = []
    for i, raw in enumerate(data.get("scenes", [])):
        narration = (raw.get("narration") or "").strip()
        if not narration:
            continue
        motion = raw.get("motion") if raw.get("motion") in MOTIONS else MOTIONS[i % len(MOTIONS)]
        scenes.append(
            {
                "index": len(scenes),
                "narration": narration,
                "visual": (raw.get("visual") or narration)[:600],
                "motion": motion,
                "hero_ids": [h for h in (raw.get("hero_ids") or []) if h in known_ids],
                "speaker": pick_speaker(raw.get("speaker"), heroes),
                "on_screen_text": (raw.get("on_screen_text") or "").strip()[:60],
            }
        )

    if not scenes:
        raise ValueError("The Director skill could not segment this script.")

    return {"title": (data.get("title") or topic or "Video").strip(), "scenes": scenes}


def estimate_scene_count(target_seconds: int) -> int:
    return max(3, min(config.MAX_SCENES, math.ceil(target_seconds / config.SECONDS_PER_SCENE)))
