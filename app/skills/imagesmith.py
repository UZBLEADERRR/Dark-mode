"""Skill 2 — Imagesmith.

Rewrites each scene's plain-language `visual` into a prompt an image model can
actually execute, keeping one consistent look across the whole video and naming
the reference characters so the generator preserves their faces.
"""

from __future__ import annotations

from .. import config
from .llm import call_json

SYSTEM = """You are the Imagesmith skill of an automated video studio.

You convert scene descriptions into prompts for a text-to-image model. Every image
in one video must look like it came from the same camera, the same colourist and the
same shoot. That consistency matters more than any single striking frame.

Rules:
- Write one prompt per scene, in English, 25-60 words.
- Structure each prompt as: subject and action, then setting, then framing and lens,
  then lighting, then the shared style suffix.
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
                "required": ["index", "prompt", "negative_prompt"],
                "properties": {
                    "index": {"type": "integer"},
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
per scene, appending that style bible to each. Return exactly {len(scenes)} prompts with
matching `index` values."""

    data = await call_json(SYSTEM, user, SCHEMA)

    style_bible = (data.get("style_bible") or art_style).strip()
    by_index = {int(p["index"]): p for p in data.get("prompts", []) if "index" in p}

    for scene in scenes:
        entry = by_index.get(scene["index"], {})
        prompt = (entry.get("prompt") or "").strip()
        if not prompt:
            prompt = f"{scene['visual']}. {style_bible}"
        elif style_bible.lower() not in prompt.lower():
            prompt = f"{prompt} {style_bible}"
        scene["image_prompt"] = prompt
        negative = (entry.get("negative_prompt") or "").strip()
        scene["negative_prompt"] = negative or DEFAULT_NEGATIVE

    return {"style_bible": style_bible, "scenes": scenes}
