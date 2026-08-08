"""Skill 2 — Imagesmith.

Rewrites each scene's plain-language `visual` into a prompt an image model can
actually execute, keeping one consistent look across the whole video and naming
the reference characters so the generator preserves their faces.
"""

from __future__ import annotations

from .. import config
from ..render import shots
from .llm import call_json

SYSTEM = """You are the Imagesmith skill of an automated video studio.

You convert scene descriptions into prompts for a text-to-image model. Every image
in one video must look like it came from the same camera, the same colourist and the
same shoot. That consistency matters more than any single striking frame.

Rules:
- Write one prompt per shot, in English, 25-60 words.
- Some scenes are covered by more than one shot. Those shots are consecutive
  seconds of the same moment, not different moments: keep the place, the people,
  the light and the time of day identical, and change only where the camera is.
  A wide, then a medium, then a detail of the same thing.
- Structure each prompt as: subject and action, then setting, then framing, then
  lighting, then the shared style suffix. Reach for camera and lens language only
  when the requested art direction is photographic — describing a flat 2D
  illustration in terms of focal lengths drags the generator back toward realism.
- Vary the framing across scenes (wide establishing, medium, close-up, over-the-shoulder,
  low angle, top-down) so the finished video does not feel static.
- When a scene lists hero reference images, describe those characters by their given
  name and say explicitly that their face and clothing must match the reference exactly.
- A character must look the same in every scene they appear in. An image model has
  no memory between calls, so the only thing holding a face together across a video
  is the words you write. First fill in `cast_bible`: one entry per character who
  appears more than once — the people with reference photos, and also anyone the
  story itself invents. Each `look` is 15-35 words of fixed, checkable physical
  detail — age, build, hair colour and style, skin tone, eye colour, exact clothing
  and its colours, and any one distinguishing feature. No mood, no lighting, no
  camera: those change between scenes and these must not.
- Then list in each prompt's `characters` field the names of whoever is in that
  shot, spelled exactly as in `cast_bible`.
- Never put words, captions, letters, logos, watermarks or UI in the image. Say so in
  the negative prompt.
- Leave visual breathing room where the subtitles will sit (lower third of the frame).
- No text rendering, no collages, no split screens, no borders.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["style_bible", "cast_bible", "prompts"],
    "properties": {
        "style_bible": {"type": "string"},
        # A list rather than a free-form object: response schemas want fixed
        # property names, and the cast is not known until the script is written.
        "cast_bible": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "look"],
                "properties": {
                    "name": {"type": "string"},
                    "look": {"type": "string"},
                },
            },
        },
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "shot", "prompt", "negative_prompt",
                             "characters"],
                "properties": {
                    "index": {"type": "integer"},
                    "shot": {"type": "integer"},
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
                    "characters": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    },
}

DEFAULT_NEGATIVE = (
    "text, letters, words, captions, subtitles, watermark, logo, signature, "
    "extra fingers, deformed hands, extra limbs, distorted face, lowres, jpeg artifacts, "
    "collage, split screen, border, frame"
)


async def build_image_prompts(
    *,
    scenes: list[dict],
    art_style: str,
    video_format: str,
    heroes: list[dict],
    title: str,
) -> dict:
    fmt = config.FORMATS.get(video_format, config.FORMATS["16:9"])
    heroes_by_id = {h["id"]: h for h in heroes}

    lines = []
    wanted = 0
    for scene in scenes:
        cast = ", ".join(
            heroes_by_id[h]["name"] for h in scene.get("hero_ids", []) if h in heroes_by_id
        )
        lines.append(
            f"[{scene['index']}] visual: {scene['visual']}\n"
            f"      narration: {scene['narration'][:200]}\n"
            f"      characters in shot: {cast or 'none'}\n"
            f"      camera move: {scene['motion']}"
        )
        # A split scene asks for one prompt per shot, each with the framing it
        # has already been assigned, so the model varies distance rather than
        # inventing a different moment for every cut.
        cuts = scene.get("shots") or []
        wanted += len(cuts) or 1
        for j, cut in enumerate(cuts):
            lines.append(
                f"      shot {j}: {shots.FRAMINGS[j % len(shots.FRAMINGS)]}")

    cast_block = (
        "\n".join(
            f"- {h['name']}: {h.get('description') or 'see reference photo'}" for h in heroes
        )
        or "(none)"
    )

    user = f"""VIDEO TITLE
{title}

REQUESTED ART DIRECTION
{art_style}

ASPECT RATIO
{fmt['aspect']} — compose for this ratio.

CHARACTER REFERENCES (photos are supplied to the image model alongside your prompt)
{cast_block}

SCENES
{chr(10).join(lines)}

First write a one-sentence `style_bible` that every prompt will end with — the shared
film stock, colour palette, and lighting language for this video.

Then write `cast_bible`: every character who appears in more than one shot, including
ones the story invents that have no reference photo. This is what keeps a face the same
from scene to scene, so be specific and be brief.

Then write one prompt per shot, appending that style bible to each, and naming in
`characters` whoever is in the shot.

Return exactly {wanted} prompts. Set `index` to the scene number and `shot` to the shot
number given above — a scene listed without shots has one prompt with `shot` 0."""

    data = await call_json(SYSTEM, user, SCHEMA)

    style_bible = (data.get("style_bible") or art_style).strip()

    # Keyed loosely: the model writes "Ali" in one place and "ali" in another, and
    # a character bible that misses on capitalisation is a character bible that
    # does nothing.
    cast: dict[str, str] = {}
    for entry in data.get("cast_bible") or []:
        name = str(entry.get("name") or "").strip()
        look = " ".join(str(entry.get("look") or "").split())[:400]
        if name and look:
            cast[name.casefold()] = f"{name}: {look}"
    # Anyone with a photo but no entry still gets one from what the user typed
    # about them — better a short description than none.
    for hero in heroes:
        key = (hero.get("name") or "").strip().casefold()
        if key and key not in cast and (hero.get("description") or "").strip():
            cast[key] = f"{hero['name']}: {' '.join(hero['description'].split())[:400]}"
    by_key: dict[tuple[int, int], dict] = {}
    for entry in data.get("prompts", []):
        if "index" not in entry:
            continue
        try:
            by_key[(int(entry["index"]), int(entry.get("shot", 0)))] = entry
        except (TypeError, ValueError):
            continue

    def dress(entry: dict, fallback_visual: str,
              named: list[str] | None = None) -> tuple[str, str]:
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            prompt = f"{fallback_visual}. {style_bible}"
        elif style_bible.lower() not in prompt.lower():
            prompt = f"{prompt} {style_bible}"

        # Appended here rather than left to the model to remember. It was asked
        # to carry the description into every prompt, and a model that forgets on
        # scene nine draws a different person there — which is the whole failure
        # this exists to prevent. Deterministic beats hopeful.
        wanted = [n for n in (entry.get("characters") or []) if isinstance(n, str)]
        wanted += named or []
        looks, seen = [], set()
        for name in wanted:
            key = name.strip().casefold()
            if key in cast and key not in seen:
                seen.add(key)
                looks.append(cast[key])
        if looks:
            prompt = (f"{prompt} The characters must look exactly like this, "
                      f"unchanged from every other scene — {'; '.join(looks)}.")
        return prompt, (entry.get("negative_prompt") or "").strip() or DEFAULT_NEGATIVE

    for scene in scenes:
        # Whoever this scene was cast with, by name — so a scene the model forgot
        # to fill in `characters` for still gets its heroes described.
        cast_here = [heroes_by_id[h]["name"] for h in scene.get("hero_ids", [])
                     if h in heroes_by_id]
        # The scene keeps a prompt of its own whatever happens: it is the
        # thumbnail, and the starting point for any shot added by hand later.
        scene["image_prompt"], scene["negative_prompt"] = dress(
            by_key.get((scene["index"], 0), {}), scene["visual"], cast_here)
        for j, cut in enumerate(scene.get("shots") or []):
            framing = shots.FRAMINGS[j % len(shots.FRAMINGS)]
            cut["prompt"], cut["negative_prompt"] = dress(
                by_key.get((scene["index"], j), {}),
                f"{scene['visual']}, {framing}", cast_here)

    return {"style_bible": style_bible, "cast_bible": cast, "scenes": scenes}
