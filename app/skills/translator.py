"""Translation that has to fit in the time the original took.

An ordinary translation optimises for accuracy alone. This one is spoken over
picture that is already cut, so length is a hard constraint: a line that takes
twice as long to say either runs over the next shot or has to be sped up until
it stops sounding human. The model is therefore asked for a translation of
roughly the same *spoken* length, and told it may reshape a sentence to get
there — which is what a real dubbing writer does.
"""

from __future__ import annotations

from .. import config
from .llm import call_json

SYSTEM = """You translate narration for dubbing.

Rules:
- Translate meaning, not words. Idioms become the natural equivalent.
- Match the *spoken length* of the original as closely as you can. The
  translation is read over picture that is already cut, so a line that takes
  much longer than the original will not fit. Prefer the shorter phrasing.
- Keep names, places and numbers exactly as they are.
- Keep the tone of the original: a documentary stays a documentary.
- Never add, remove or merge lines. Return exactly one translation per line, in
  the same order, with the same index.
- Write nothing but the spoken words — no speaker labels, no notes, no
  timestamps, no quotation marks around the line."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "lines": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "text": {"type": "STRING"},
                },
                "required": ["index", "text"],
                "propertyOrdering": ["index", "text"],
            },
        }
    },
    "required": ["lines"],
}

# One request per batch of lines: small enough that a long video does not blow
# the output limit, large enough that the model still sees the context around
# each line and keeps the terminology consistent.
BATCH = 25


async def translate_lines(
    *,
    lines: list[str],
    target_language: str,
    source_language: str = "",
    tone: str = "",
    durations: list[float] | None = None,
) -> list[str]:
    """Translate in order. A line that fails to come back is left as it was."""
    if not lines:
        return []

    target = config.LANGUAGES.get(target_language, target_language)
    source = config.LANGUAGES.get(source_language, source_language)
    out = list(lines)

    for start in range(0, len(lines), BATCH):
        chunk = list(enumerate(lines[start:start + BATCH], start=start))
        listing = "\n".join(
            f"[{i}] ({durations[i]:.1f}s) {text}" if durations and i < len(durations)
            else f"[{i}] {text}"
            for i, text in chunk
        )
        user = f"""TRANSLATE INTO
{target}

{f'THE ORIGINAL IS IN{chr(10)}{source}{chr(10)}' if source else ''}{f'TONE{chr(10)}{tone}{chr(10)}' if tone else ''}
LINES
Each line shows its index and, where known, how many seconds it takes to say in
the original. Aim for a translation that takes about that long to speak.

{listing}

Return {len(chunk)} translations with matching index values."""

        try:
            data = await call_json(SYSTEM, user, SCHEMA, max_tokens=8000)
        except Exception:  # noqa: BLE001 - an untranslated line beats no video
            continue

        for entry in data.get("lines", []):
            try:
                index = int(entry["index"])
            except (KeyError, TypeError, ValueError):
                continue
            text = str(entry.get("text", "")).strip()
            if text and 0 <= index < len(out):
                out[index] = text

    return out
