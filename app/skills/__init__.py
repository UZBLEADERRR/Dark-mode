"""AI "skills" — each one is a focused Claude call with its own system prompt.

    director   -> turns a topic into a timed scene-by-scene script
                  (or storyboards narration the user already recorded)
    imagesmith -> turns each scene into an image-generation prompt
    subtitler  -> turns word timings into readable, well-broken caption lines
    publisher  -> title, description, tags, thumbnail prompt, music mood
    translator -> re-voices a script in another language, to the same length
"""

from .director import (
    direct_script,
    segment_existing_narration,
    segment_written_script,
)
from .imagesmith import build_image_prompts
from .publisher import build_publish_pack
from .subtitler import build_captions
from .translator import translate_lines

__all__ = [
    "direct_script",
    "segment_existing_narration",
    "segment_written_script",
    "build_image_prompts",
    "build_captions",
    "build_publish_pack",
    "translate_lines",
]
