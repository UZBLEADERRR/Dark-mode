"""Skill 10 — Arranger.

Takes a folder of pictures that were made somewhere else — in Google Flow, in
another generator, or with a camera — and works out which scene each one belongs
to.

This is the other half of making the images by hand. The prompts leave Sarideo in
scene order, but they do not come back in scene order: Flow downloads them named
after itself, the browser sorts them by the second they landed, and a hundred
files arrive as a heap. Sorting that heap by hand is the whole cost of not paying
an image API, so the model does it: it looks at each picture, reads what each
scene asked for, and says where the picture goes.

Positional order is still the right answer when the pile really is in order, and
it costs nothing — so it is a mode of its own, and it is also what a failed model
call falls back to.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .llm import call_json

SYSTEM = """You are the Arranger skill of an automated video studio.

You are shown pictures that were generated for one video, and the list of scenes
that video is made of. Your job is to say which scene each picture was made for.

The pictures arrive in no particular order and their filenames mean nothing. Judge
only by what is in the frame against what the scene asked for: the subject, the
setting, the characters, the time of day, the framing.

Rules:
- Answer for every picture you are shown, using the numbers given.
- `scene` is the scene number the picture belongs to, or -1 when the picture
  clearly belongs to none of them.
- `confidence` is 0-100. Be honest and use the low end: two scenes described in
  similar words are a coin toss, and saying so lets the studio break the tie by
  file order instead of trusting a guess.
- Several pictures may look right for the same scene. Say so anyway — the studio
  keeps the best one and moves the rest to the scenes still waiting.
- One short clause for `why`, naming the thing in the frame that decided it.
"""

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["matches"],
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["picture", "scene", "confidence", "why"],
                "properties": {
                    "picture": {"type": "integer"},
                    "scene": {"type": "integer"},
                    "confidence": {"type": "integer"},
                    "why": {"type": "string"},
                },
            },
        },
    },
}

# How many pictures go into one model call. Small enough that a hundred images
# never becomes one enormous request that times out halfway, large enough that a
# normal video is one or two calls.
BATCH = 12

# How many of those calls run at once. The batches are independent — each one is
# scored against the same scene list — so they overlap, but not so far that a
# rate limit turns a hundred pictures into a hundred refusals.
WORKERS = 3


def _scene_lines(scenes: list[dict]) -> str:
    lines = []
    for scene in scenes:
        want = (scene.get("image_prompt") or scene.get("visual") or "").strip()
        said = (scene.get("narration") or "").strip()
        lines.append(
            f"[{scene['index']}] {want[:400] or '(no prompt)'}\n"
            f"      narration: {said[:160] or '(silent)'}"
        )
    return "\n".join(lines) or "(no scenes)"


async def _one_batch(
    scene_block: str, batch: list[dict], first: int, total: int,
) -> list[dict]:
    listing = "\n".join(
        f"picture {first + i}: filename {p['name']}" for i, p in enumerate(batch))
    user = f"""SCENES IN THIS VIDEO
{scene_block}

PICTURES IN THIS BATCH
The images are attached in the order listed here.
{listing}

There are {total} pictures in total; you are seeing {len(batch)} of them now.
Return one match for each of the {len(batch)} pictures listed above, using the
picture numbers exactly as given."""

    data = await call_json(
        SYSTEM, user, SCHEMA,
        images=[(p["thumb"], p["mime"]) for p in batch],
    )

    out: list[dict] = []
    for entry in data.get("matches") or []:
        try:
            picture = int(entry["picture"])
            scene = int(entry["scene"])
        except (KeyError, TypeError, ValueError):
            continue
        if not first <= picture < first + len(batch):
            continue
        try:
            confidence = int(entry.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        out.append({
            "picture": picture,
            "scene": scene,
            "confidence": max(0, min(100, confidence)),
            "why": (entry.get("why") or "").strip()[:200],
        })
    return out


def place_in_order(pictures: list[dict], scenes: list[dict]) -> list[dict]:
    """Scene order, file order, straight down the list — no model involved.

    What you want when the pile genuinely is in order, which is the normal case
    when the prompts were worked through one at a time.
    """
    return [
        {"picture": i, "scene": scene["index"], "confidence": 0, "why": "file order"}
        for i, (scene, _p) in enumerate(zip(scenes, pictures))
    ]


def _settle(
    proposals: list[dict], pictures: list[dict], scenes: list[dict],
) -> list[dict]:
    """Turn a heap of opinions into one picture per scene.

    Confident matches are honoured first, so a picture the model was sure about
    takes its scene before a coin-toss can claim it. What is left over is dealt
    out in file order — which is the answer that was always going to be right for
    a pile that came out in order, and is no worse than a guess for one that did
    not.
    """
    wanted = [s["index"] for s in scenes]
    taken_scene: dict[int, dict] = {}
    used_picture: set[int] = set()

    for entry in sorted(proposals, key=lambda e: -e["confidence"]):
        if entry["picture"] in used_picture or entry["scene"] in taken_scene:
            continue
        if entry["scene"] not in wanted:
            continue
        taken_scene[entry["scene"]] = entry
        used_picture.add(entry["picture"])

    spare = [i for i in range(len(pictures)) if i not in used_picture]
    empty = [index for index in wanted if index not in taken_scene]
    for index, picture in zip(empty, spare):
        taken_scene[index] = {
            "picture": picture, "scene": index, "confidence": 0,
            "why": "left over — placed in file order",
        }

    return [taken_scene[i] for i in wanted if i in taken_scene]


async def arrange_images(
    *,
    pictures: list[dict[str, Any]],
    scenes: list[dict],
    on_progress: Any = None,
) -> list[dict]:
    """Match pictures to scenes. Returns one entry per scene that got one.

    `pictures` is `[{"name": str, "thumb": bytes, "mime": str}]` — thumbnails,
    not the originals: the model only has to recognise the picture, and sending a
    hundred full-size stills would cost more than the images did.
    """
    if not pictures or not scenes:
        return []

    scene_block = _scene_lines(scenes)
    batches = [pictures[i:i + BATCH] for i in range(0, len(pictures), BATCH)]
    gate = asyncio.Semaphore(WORKERS)
    done = 0
    lock = asyncio.Lock()

    async def run(n: int, batch: list[dict]) -> list[dict]:
        nonlocal done
        async with gate:
            try:
                found = await _one_batch(
                    scene_block, batch, n * BATCH, len(pictures))
            except Exception:  # noqa: BLE001 - one bad batch is not the whole job
                found = []
            async with lock:
                done += 1
                if on_progress:
                    on_progress(done, len(batches))
            return found

    results = await asyncio.gather(*(run(n, b) for n, b in enumerate(batches)))
    proposals = [entry for batch in results for entry in batch]
    if not proposals:
        # Every call failed. File order is a worse answer than the model's, and a
        # much better one than leaving every scene empty.
        proposals = place_in_order(pictures, scenes)
    return _settle(proposals, pictures, scenes)
