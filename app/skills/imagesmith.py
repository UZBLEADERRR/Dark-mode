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
- Never put words, captions, letters, logos, watermarks or UI in the image. Say so in
  the negative prompt.
- Leave visual breathing room where the subtitles will sit (lower third of the frame).
- No text rendering, no collages, no split screens, no borders.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["style_bible", "prompts"],
    "properties": {
        "style_bible": {"type": "string"},
        "prompts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "shot", "prompt", "negative_prompt"],
                "properties": {
                    "index": {"type": "integer"},
                    "shot": {"type": "integer"},
                    "prompt": {"type": "string"},
                    "negative_prompt": {"type": "string"},
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
film stock, colour palette, and lighting language for this video. Then write one prompt
per shot, appending that style bible to each.

Return exactly {wanted} prompts. Set `index` to the scene number and `shot` to the shot
number given above — a scene listed without shots has one prompt with `shot` 0."""

    data = await call_json(SYSTEM, user, SCHEMA)

    style_bible = (data.get("style_bible") or art_style).strip()
    by_key: dict[tuple[int, int], dict] = {}
    for entry in data.get("prompts", []):
        if "index" not in entry:
            continue
        try:
            by_key[(int(entry["index"]), int(entry.get("shot", 0)))] = entry
        except (TypeError, ValueError):
            continue

    def dress(entry: dict, fallback_visual: str) -> tuple[str, str]:
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            prompt = f"{fallback_visual}. {style_bible}"
        elif style_bible.lower() not in prompt.lower():
            prompt = f"{prompt} {style_bible}"
        return prompt, (entry.get("negative_prompt") or "").strip() or DEFAULT_NEGATIVE

    for scene in scenes:
        # The scene keeps a prompt of its own whatever happens: it is the
        # thumbnail, and the starting point for any shot added by hand later.
        scene["image_prompt"], scene["negative_prompt"] = dress(
            by_key.get((scene["index"], 0), {}), scene["visual"])
        for j, cut in enumerate(scene.get("shots") or []):
            framing = shots.FRAMINGS[j % len(shots.FRAMINGS)]
            cut["prompt"], cut["negative_prompt"] = dress(
                by_key.get((scene["index"], j), {}), f"{scene['visual']}, {framing}")

    return {"style_bible": style_bible, "scenes": scenes}
