"""Fixing a script by saying what is wrong with it.

The other way to fix a script is to retype it, and for one line that is faster.
This is for the note you would give a writer — "the third scene is dry", "stop
saying 'imagine'", "make the ending land" — where the change is a judgement
rather than a specific set of words, and where it touches several lines at once.

Two rules make it safe to run on a script that is already timed:

**The scene count never changes.** Scenes are what pictures and voice-overs are
attached to, so a rewrite that merged two of them would orphan whatever was
already made. The model may move a sentence between scenes; it may not add or
drop a scene.

**Untouched is a real answer.** A note about the opening should leave the other
nine scenes byte-identical, so the app can say exactly what changed, and so a
second note does not quietly re-write everything a first note got right.
"""

from __future__ import annotations

from typing import Any

from .llm import call_json

SYSTEM = """You revise a video narration to a note from the person whose video it
is.

You get the script as numbered scenes and one instruction. Apply the
instruction — nothing else.

Rules:
- Return exactly the same number of scenes, with the same numbers. Never add,
  drop, merge or reorder a scene.
- A scene the note does not touch comes back **character for character
  identical**. Do not tidy it, do not improve it, do not re-punctuate it. If you
  return it changed, you have failed the instruction.
- Keep each scene close to its original length unless the note asks otherwise.
  The pictures are cut to this timing, and a line twice as long is a scene that
  runs over its own picture.
- Write only what is spoken. No scene headings, no stage directions, no
  timestamps, no quotation marks around the line.
- Stay in the language the script is written in, unless the note asks for
  another one.
- If the note asks for something you cannot do — a fact you do not have, a
  change that would need a different video — do the part you can and say what
  you did not do in `note_back`, in the script's language.

`note_back` is one short sentence to the user: what you changed, or what you
could not. Not a summary of the script."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "scenes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "narration": {"type": "STRING"},
                },
                "required": ["index", "narration"],
                "propertyOrdering": ["index", "narration"],
            },
        },
        "note_back": {"type": "STRING"},
    },
    "required": ["scenes"],
}


def describe(scenes: list[dict[str, Any]]) -> str:
    return "\n".join(f"[{s['index']}] {str(s.get('narration') or '').strip()}"
                     for s in scenes)


def apply_revision(scenes: list[dict[str, Any]], raw: Any) -> tuple[int, str]:
    """Copy the rewritten lines onto the scenes. Returns (how many changed, note).

    Only lines that came back different are touched, and a line that comes back
    empty is ignored rather than allowed to blank a scene — a model that loses
    its place mid-answer should cost nothing.
    """
    if not isinstance(raw, dict):
        return 0, ""
    by_index = {int(s["index"]): s for s in scenes if "index" in s}
    changed = 0
    for item in raw.get("scenes") or []:
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        scene = by_index.get(index)
        if scene is None:
            continue
        line = str(item.get("narration") or "").strip()
        if not line or line == str(scene.get("narration") or "").strip():
            continue
        scene["narration"] = line
        # The old timings belong to the old words, and the picture and voice made
        # from them are now stale.
        scene["words"] = []
        scene["needs_voice"] = True
        changed += 1
    return changed, str(raw.get("note_back") or "").strip()[:400]


async def revise_script(
    *,
    scenes: list[dict[str, Any]],
    note: str,
    language: str,
    tone: str = "",
    title: str = "",
) -> tuple[int, str]:
    """Rewrite the script to a note, in place. Returns (changed, note back)."""
    if not scenes or not note.strip():
        return 0, ""

    user = (
        f"Video title: {title or '(untitled)'}\n"
        f"Language: {language}\n"
        + (f"Tone: {tone}\n" if tone else "")
        + f"\nThe instruction:\n{note.strip()}\n\nThe script:\n"
        + describe(scenes)
    )
    # Room for the whole script back plus the model's thinking. A script is the
    # longest thing this app asks a model to return, and a rewrite returns all
    # of it — including the scenes it is leaving alone.
    budget = max(8000, 1500 + sum(len(str(s.get("narration") or "")) for s in scenes) * 2)
    raw = await call_json(SYSTEM, user, SCHEMA, max_tokens=min(budget, 32000))
    return apply_revision(scenes, raw)
