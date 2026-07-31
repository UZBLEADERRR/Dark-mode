"""AI "skills" — each one is a focused Claude call with its own system prompt.

    director   -> turns a topic into a timed scene-by-scene script
                  (or storyboards narration the user already recorded)
    imagesmith -> turns each scene into an image-generation prompt
    subtitler  -> turns word timings into readable, well-broken caption lines
    choreographer -> stages a cartoon: who is on screen, where, and how they move
    publisher  -> title, description, tags, thumbnail prompt, music mood
    translator -> re-voices a script in another language, to the same length
    strategist -> reads your own channels and talks you from an idea to a video
    shorts     -> finds the Shorts already inside a long video
    rewriter   -> revises a script to a note, before anything is made from it
"""

from .director import (
    direct_script,
    segment_existing_narration,
    segment_written_script,
)
from .choreographer import stage_scenes
from .rewriter import revise_script
from .shorts import suggest_shorts
from .imagesmith import build_image_prompts
from .publisher import build_publish_pack
from .strategist import chat, read_profile
from .subtitler import build_captions
from .translator import translate_lines

__all__ = [
    "direct_script",
    "segment_existing_narration",
    "segment_written_script",
    "build_image_prompts",
    "stage_scenes",
    "build_captions",
    "build_publish_pack",
    "translate_lines",
    "chat",
    "read_profile",
    "suggest_shorts",
    "revise_script",
]
