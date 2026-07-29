"""Skill 6 — Choreographer.

Turns a storyboard into staging: for each scene, which characters are on screen,
where they stand, and how they move across it.

This is the difference between an explainer and a cartoon. An explainer draws the
whole picture — character and background together — and slides the camera over
it. A cartoon draws the background on its own, cuts the characters out, and moves
them across it. Nothing here is drawn frame by frame; a cut-out that walks over a
still is the whole of what paper animation ever was, and it is enough.

Two things follow from that and both are this skill's job:

* the background prompt must not contain the characters, or they arrive twice —
  once painted into the scenery and once walking over it;
* somebody has to decide who enters from where and when, and that decision is
  what makes the scene read as an action rather than as a sticker on a photo.
"""

from __future__ import annotations

from .. import config
from ..render import overlays as ov
from .llm import call_json

# What the model is allowed to ask for. Kept in step with the renderer by
# reading its table rather than by repeating it.
MOVES = list(ov.ACTOR_MOVES)

SYSTEM = """You are the Choreographer skill of an automated cartoon studio.

You are given a storyboard that already has narration and a visual description for
every scene, plus a cast of characters the studio has reference art for. You decide
the staging: who is on screen in each scene, where they stand, and how they move.

How the render works, because it constrains what you can ask for:
- A character is drawn once per POSE you ask for, cut out of its background, and
  then MOVED across a still background. Within a scene the drawing does not change —
  it does not walk its legs or blink. The movement across the frame is the only
  animation there is; the change between scenes comes from asking for a different
  pose.
- Every distinct pose costs one generated picture, so ask for a new one when the
  character's attitude genuinely changes, and repeat an earlier one word for word
  when it does not.
- The background is generated separately, from your `background` prompt, and the
  characters are laid on top of it.

Rules that matter:
- `background` must describe the setting ALONE: the place, the light, the weather,
  the props. Never mention the characters, people, figures, or anyone standing
  anywhere. If they appear in the background they will also appear as cut-outs, and
  the scene will show two of everybody. Write it in English.
- `actors` lists who is on screen. Use only ids from the cast. Most scenes have one
  or two; a scene can have none, and an establishing shot of a place usually should.
- `pose` is what the character LOOKS like in this scene: the body attitude and the
  face. This is drawn fresh for each distinct pose, so it is how a scene gets a
  frightened character rather than the same smiling one every time. Write it in
  English, 4-12 words, describing only the character: "running, terrified, looking
  back over one shoulder", "standing tall, arms crossed, confident smile",
  "crouching low behind a rock, wide-eyed". Never mention the setting, the
  background, other characters, or a camera. Reuse the exact same wording when the
  attitude genuinely repeats — an identical pose costs nothing, a slightly reworded
  one is drawn again.
- `move` is what the character does. Read the narration and choose the one that
  matches the action being described:
  walk_right / walk_left — crossing part of the frame, going somewhere
  enter_left / enter_right — arriving into the shot from off screen
  exit_left / exit_right — leaving the shot
  cross_right / cross_left — passing all the way through, chasing or fleeing
  hop — excitement, surprise, landing
  sway — standing and talking, breathing, listening
- A character who is speaking this line should usually `sway` or stand — do not send
  someone walking out of frame while they are still talking.
- `x` is where they stand across the frame, 0 is the left edge and 1 the right.
  `y` is where their feet are, 0 is the top and 1 the bottom; characters stand on the
  ground, so `y` is normally between 0.55 and 0.8. Two characters in one scene must
  not share an `x` — keep them at least 0.25 apart or they will overlap.
- `size` is how tall the character is as a fraction of the frame, 0.2 to 0.6. Someone
  close to camera is bigger; someone far away is smaller. Use it for depth.
- `enters_at` is how many seconds into the scene the move begins. 0 for most; give a
  character who arrives partway through a second or two so the shot is established
  before something walks into it.
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
                "required": ["index", "background", "actors"],
                "properties": {
                    "index": {"type": "integer"},
                    "background": {"type": "string"},
                    "actors": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["hero_id", "pose", "move", "x", "y", "size",
                                         "enters_at"],
                            "properties": {
                                "hero_id": {"type": "string"},
                                "pose": {"type": "string"},
                                "move": {"type": "string", "enum": MOVES},
                                "x": {"type": "number"},
                                "y": {"type": "number"},
                                "size": {"type": "number"},
                                "enters_at": {"type": "number"},
                            },
                        },
                    },
                },
            },
        },
    },
}

# At most this many cut-outs in one scene. Beyond three the frame is a crowd of
# stickers, and every one of them is another overlay in the render graph.
MAX_ACTORS = 3


def _clamp(value: object, low: float, high: float, fallback: float) -> float:
    try:
        return round(max(low, min(high, float(value))), 3)
    except (TypeError, ValueError):
        return fallback


def _space_out(actors: list[dict]) -> list[dict]:
    """Push apart two characters the model put in the same place.

    Overlapping cut-outs look like one broken drawing, and it is the single most
    visible way for this to go wrong — so it is corrected here rather than
    trusted to the prompt.
    """
    ordered = sorted(actors, key=lambda a: a["x"])
    for earlier, later in zip(ordered, ordered[1:]):
        if later["x"] - earlier["x"] < 0.22:
            later["x"] = round(min(0.92, earlier["x"] + 0.22), 3)
    return ordered


async def stage_scenes(
    *,
    scenes: list[dict],
    heroes: list[dict],
    action: str,
    language: str,
    video_format: str,
) -> list[dict]:
    """Return one staging entry per scene: a background prompt and its actors."""
    if not heroes:
        return []

    cast = "\n".join(
        f"- id: {h['id']} | name: {h['name']}"
        + (f" | {h['description']}" if h.get("description") else "")
        for h in heroes
    )
    fmt = config.FORMATS.get(video_format, config.FORMATS["16:9"])
    board = "\n".join(
        f"{s['index']}. [{s.get('visual', '')[:160]}] {s['narration'][:280]}"
        for s in scenes
    )

    user = f"""CAST
{cast}

WHAT THE USER ASKED TO HAPPEN
{action.strip() or "(nothing specific — stage it from the narration)"}

ASPECT RATIO
{fmt['aspect']} ({fmt['label']})

STORYBOARD — index. [visual] narration
{board}

Stage every one of the {len(scenes)} scenes. Return an entry for each index,
including scenes where nobody is on screen (empty `actors`)."""

    data = await call_json(SYSTEM, user, SCHEMA)

    known = {h["id"] for h in heroes}
    by_index: dict[int, dict] = {}
    for raw in data.get("scenes", []):
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue

        actors: list[dict] = []
        seen: set[str] = set()
        for entry in raw.get("actors") or []:
            hero_id = str(entry.get("hero_id") or "")
            # One cut-out per character per scene: the same still placed twice
            # is a twin, not a crowd.
            if hero_id not in known or hero_id in seen:
                continue
            seen.add(hero_id)
            move = entry.get("move")
            actors.append({
                "hero_id": hero_id,
                "pose": (str(entry.get("pose") or "").strip()[:160]
                         or "standing, calm, facing the viewer"),
                "move": move if move in ov.ACTOR_MOVES else "sway",
                "x": _clamp(entry.get("x"), 0.08, 0.92, 0.5),
                "y": _clamp(entry.get("y"), 0.35, 0.9, 0.68),
                "size": _clamp(entry.get("size"), 0.15, 0.7, 0.34),
                "enters_at": _clamp(entry.get("enters_at"), 0.0, 30.0, 0.0),
            })
            if len(actors) >= MAX_ACTORS:
                break

        by_index[index] = {
            "background": (raw.get("background") or "").strip()[:600],
            "actors": _space_out(actors),
        }

    # Every scene gets an entry, whether the model returned one or not: a scene
    # the Choreographer skipped should keep its ordinary generated picture
    # rather than end up with a background it never described.
    return [by_index.get(s["index"], {"background": "", "actors": []}) for s in scenes]
