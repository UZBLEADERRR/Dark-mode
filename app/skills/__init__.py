"""AI "skills" — each one is a focused Claude call with its own system prompt.

    director   -> turns a topic into a timed scene-by-scene script
                  (or storyboards narration the user already recorded)
    imagesmith -> turns each scene into an image-generation prompt
    subtitler  -> turns word timings into readable, well-broken caption lines
    publisher  -> title, description, tags, thumbnail prompt, music mood
"""

from .director import direct_script, segment_existing_narration
from .imagesmith import build_image_prompts
from .publisher import build_publish_pack
from .subtitler import build_captions

__all__ = [
    "direct_script",
    "segment_existing_narration",
    "build_image_prompts",
    "build_captions",
    "build_publish_pack",
]
