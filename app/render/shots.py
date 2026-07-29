"""Shots: more than one picture inside a single scene.

A scene is a unit of narration — one recorded line, one place on the timeline.
A shot is a unit of picture. Until now the two were the same thing, so a
fifteen-second line sat on one still for fifteen seconds, which reads as slow no
matter how good the still is.

Splitting them lets a line be covered by two or three pictures, each with its own
camera move and its own way of arriving. The scene keeps its identity: the audio,
the captions, the overlays and the position in the running order all still belong
to the scene, and the shots only divide the picture between them.

A scene with no shots is a scene with one shot. That is what every existing job
is, so nothing needs migrating — `plan()` synthesises the single shot from the
scene's own image and motion.
"""

from __future__ import annotations

from typing import Any

# Four is the ceiling. Past that a scene stops reading as a scene and starts
# reading as a montage, and every extra shot is another image to pay for.
MAX_PER_SCENE = 4

# No shot shorter than this: below about a second the eye registers a flash
# rather than a picture, and a Ken Burns move has no room to travel.
MIN_SECONDS = 0.9

# How the framing changes as shots are added to a scene. The generator is much
# better at "the same moment from a different distance" than at inventing a new
# moment, and a cut between two distances is the oldest trick in editing.
FRAMINGS = [
    "wide establishing shot, full environment visible",
    "medium shot, subject from the waist up",
    "tight close-up on the most important detail",
    "low angle, looking up past the subject",
]

# Each shot after the first gets a different move, so a fast cut does not land on
# the same drift twice running.
MOTION_CYCLE = ["zoom_in", "pan_right", "zoom_out", "pan_left"]


def blank(index: int = 0) -> dict[str, Any]:
    return {
        "prompt": "",
        "negative_prompt": "",
        "motion": MOTION_CYCLE[index % len(MOTION_CYCLE)],
        "motion_strength": 1.0,
        "transition": "",
        "weight": 1.0,
        "needs_image": True,
    }


def wanted_count(words: int, pace: str) -> int:
    """How many shots a line of this length should get at this pace.

    Word count rather than seconds, because shots are decided while the image
    prompts are written and the narration has not been recorded yet. Speech runs
    close enough to 2.5 words a second for this to land within a shot either way.
    """
    seconds = max(1.0, words / 2.5)
    per_shot = {"steady": 0.0, "dynamic": 4.5, "fast": 2.8}.get(pace, 0.0)
    if not per_shot:
        return 1
    # Nearest, not floor: at one shot every 4.5 seconds an eight-second line
    # wants two pictures, and flooring would give it one.
    return max(1, min(MAX_PER_SCENE, int(seconds / per_shot + 0.5)))


def normalize(raw: Any, index: int = 0) -> dict[str, Any] | None:
    """Clean one shot as it arrives from the editor. Junk becomes None."""
    if not isinstance(raw, dict):
        return None
    shot = blank(index)
    shot.update({
        "prompt": str(raw.get("prompt") or "").strip()[:2000],
        "negative_prompt": str(raw.get("negative_prompt") or "").strip()[:1000],
        "motion": str(raw.get("motion") or shot["motion"]),
        "transition": str(raw.get("transition") or "").strip(),
    })
    try:
        shot["motion_strength"] = max(0.2, min(2.5, float(raw.get("motion_strength", 1.0))))
    except (TypeError, ValueError):
        shot["motion_strength"] = 1.0
    try:
        # Weights are relative, so the absolute value never matters — only the
        # ratio between the shots of one scene.
        shot["weight"] = max(0.25, min(4.0, float(raw.get("weight", 1.0))))
    except (TypeError, ValueError):
        shot["weight"] = 1.0

    for key in ("sid", "image_path", "image_version", "needs_image"):
        if key in raw:
            shot[key] = raw[key]
    return shot


def normalize_all(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    cleaned = [s for s in (normalize(r, i) for i, r in enumerate(raw)) if s]
    return cleaned[:MAX_PER_SCENE]


def of(scene: dict) -> list[dict[str, Any]]:
    """The scene's shots, or the implicit single shot it has always had."""
    shots = scene.get("shots") or []
    if shots:
        return shots
    return [{
        "sid": f"{scene.get('sid', scene.get('index', 0))}-0",
        "prompt": scene.get("image_prompt", ""),
        "negative_prompt": scene.get("negative_prompt", ""),
        "motion": scene.get("motion", "zoom_in"),
        "motion_strength": float(scene.get("motion_strength") or 1.0),
        "transition": "",
        "weight": 1.0,
        "image_path": scene.get("image_path"),
        "image_version": scene.get("image_version", 0),
        "needs_image": bool(scene.get("needs_image")),
        "implicit": True,
    }]


def is_split(scene: dict) -> bool:
    return len(scene.get("shots") or []) > 1


def plan(scene: dict, total: float, inner: float = 0.0) -> list[dict[str, Any]]:
    """Divide the scene's screen time between its shots.

    `inner` is the cross-fade between shots. A fade consumes time from both
    sides, so the slices are grown to pay for it — exactly as the top level
    lengthens each scene clip by the transition it will lose. The result always
    covers `total` once the fades have eaten their overlap.
    """
    shots = of(scene)
    count = len(shots)
    if count == 1:
        return [{**shots[0], "seconds": max(MIN_SECONDS, total)}]

    budget = total + inner * (count - 1)
    weights = [max(0.25, float(s.get("weight") or 1.0)) for s in shots]
    share = sum(weights)
    seconds = [budget * w / share for w in weights]

    # A shot squeezed under the floor steals from the longest one rather than
    # from everybody, so the fix stays local and the ratios elsewhere survive.
    for i, value in enumerate(seconds):
        if value >= MIN_SECONDS:
            continue
        donor = max(range(len(seconds)), key=lambda j: seconds[j])
        if donor == i or seconds[donor] - (MIN_SECONDS - value) < MIN_SECONDS:
            # Nothing to take: the scene is too short to split this many ways.
            return [{**shots[0], "seconds": max(MIN_SECONDS, total)}]
        seconds[donor] -= MIN_SECONDS - value
        seconds[i] = MIN_SECONDS

    return [{**shot, "seconds": secs} for shot, secs in zip(shots, seconds)]


def backfill_prompts(scene: dict) -> None:
    """Make sure no two shots of a scene would draw the same picture.

    The Imagesmith is asked for a prompt per shot, but a model that answers with
    one prompt per scene must not quietly produce three identical stills — the
    scene would cut twice and never appear to change. Anything left empty gets
    the scene's own prompt with its framing appended, which is the fallback the
    manual "add a shot" button uses too.
    """
    base = (scene.get("image_prompt") or scene.get("visual") or "").strip()
    for i, shot in enumerate(scene.get("shots") or []):
        if (shot.get("prompt") or "").strip():
            continue
        framing = FRAMINGS[i % len(FRAMINGS)]
        shot["prompt"] = f"{base} {framing}".strip() if base else framing
        shot.setdefault("negative_prompt", scene.get("negative_prompt", ""))
