"""Central configuration, read once from the environment.

Every provider is optional: the app boots with whatever keys are present and
reports the rest as unavailable through /api/health, so a partially configured
deployment still starts instead of crashing at import time.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _flag(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if not raw:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


# --- storage -----------------------------------------------------------------
DATA_DIR = Path(_env("DATA_DIR", "./data")).resolve()
PROJECTS_DIR = DATA_DIR / "projects"
HEROES_DIR = DATA_DIR / "heroes"
MUSIC_DIR = DATA_DIR / "music"
DB_PATH = DATA_DIR / "app.db"

STORAGE_BACKEND = _env("STORAGE_BACKEND", "local").lower()  # local | supabase
SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = _env("SUPABASE_BUCKET", "videos")

# --- LLM (Claude) ------------------------------------------------------------
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
LLM_MODEL = _env("LLM_MODEL", "claude-opus-5")
LLM_EFFORT = _env("LLM_EFFORT", "high")

# --- image generation --------------------------------------------------------
IMAGE_PROVIDER = _env("IMAGE_PROVIDER", "gemini").lower()  # gemini | fal | openai

GEMINI_API_KEY = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
GEMINI_IMAGE_MODEL = _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_BASE = _env("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")

FAL_KEY = _env("FAL_KEY")
FAL_IMAGE_MODEL = _env("FAL_IMAGE_MODEL", "fal-ai/flux-pro/kontext/max/multi")
FAL_TEXT2IMG_MODEL = _env("FAL_TEXT2IMG_MODEL", "fal-ai/flux/dev")

OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE = _env("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_IMAGE_MODEL = _env("OPENAI_IMAGE_MODEL", "gpt-image-1")

# --- text to speech ----------------------------------------------------------
TTS_PROVIDER = _env("TTS_PROVIDER", "elevenlabs").lower()  # elevenlabs | openai | gemini

ELEVENLABS_API_KEY = _env("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = _env("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")
ELEVENLABS_MODEL = _env("ELEVENLABS_MODEL", "eleven_multilingual_v2")

OPENAI_TTS_MODEL = _env("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = _env("OPENAI_TTS_VOICE", "alloy")

GEMINI_TTS_MODEL = _env("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = _env("GEMINI_TTS_VOICE", "Kore")

# --- subtitle alignment ------------------------------------------------------
# elevenlabs -> character timestamps come free with the TTS call
# whisper    -> OpenAI transcription with word timestamps
# estimate   -> proportional split by character count (no extra API needed)
ALIGN_PROVIDER = _env("ALIGN_PROVIDER", "auto").lower()
OPENAI_TRANSCRIBE_MODEL = _env("OPENAI_TRANSCRIBE_MODEL", "whisper-1")

# --- rendering ---------------------------------------------------------------
FPS = _int("FPS", 30)
TRANSITION_SECONDS = float(_env("TRANSITION_SECONDS", "0.6"))
VIDEO_CRF = _int("VIDEO_CRF", 20)
VIDEO_PRESET = _env("VIDEO_PRESET", "medium")
SECONDS_PER_SCENE = float(_env("SECONDS_PER_SCENE", "6.5"))
MAX_SCENES = _int("MAX_SCENES", 90)
SUBTITLE_FONT = _env("SUBTITLE_FONT", "DejaVu Sans")
MUSIC_VOLUME = float(_env("MUSIC_VOLUME", "0.10"))
GEMINI_USE_IMAGE_CONFIG = _flag("GEMINI_USE_IMAGE_CONFIG", True)
IMAGE_CONCURRENCY = _int("IMAGE_CONCURRENCY", 3)
TTS_CONCURRENCY = _int("TTS_CONCURRENCY", 3)
MAX_CONCURRENT_JOBS = _int("MAX_CONCURRENT_JOBS", 1)

# --- video formats -----------------------------------------------------------
FORMATS: dict[str, dict] = {
    "16:9": {"label": "YouTube (16:9)", "width": 1920, "height": 1080, "aspect": "16:9"},
    "9:16": {"label": "Shorts / Reels (9:16)", "width": 1080, "height": 1920, "aspect": "9:16"},
    "1:1": {"label": "Square (1:1)", "width": 1080, "height": 1080, "aspect": "1:1"},
    "4:5": {"label": "Portrait (4:5)", "width": 1080, "height": 1350, "aspect": "4:5"},
}

LANGUAGES: dict[str, str] = {
    "en": "English",
    "uz": "Uzbek (O'zbekcha)",
    "ru": "Russian (Русский)",
    "tr": "Turkish (Türkçe)",
    "es": "Spanish (Español)",
    "ar": "Arabic (العربية)",
    "hi": "Hindi (हिन्दी)",
    "de": "German (Deutsch)",
    "fr": "French (Français)",
}


def caption_budget(width: int, height: int) -> dict[str, int | float]:
    """How wide a caption line may be, for this canvas.

    A 9:16 frame is 1080px across where 16:9 is 1920, so a line length tuned for
    landscape runs straight off the edge of a Short. Both the line-breaking skill
    and the ASS font size derive from this one budget so they can never disagree.
    """
    ratio = width / max(height, 1)
    if ratio >= 1.3:            # 16:9 and wider
        max_chars, max_words, margin = 42, 7, 0.08
    elif ratio >= 0.9:          # square
        max_chars, max_words, margin = 30, 5, 0.07
    else:                       # 9:16, 4:5 and other portrait
        max_chars, max_words, margin = 24, 4, 0.06

    usable = width * (1 - 2 * margin)
    # DejaVu Sans Bold averages ~0.55em of advance per character.
    font_size = int(usable / (max_chars * 0.55))
    return {
        "max_chars": max_chars,
        "max_words": max_words,
        "margin": margin,
        "font_size": max(20, min(font_size, int(height / 9))),
    }


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROJECTS_DIR, HEROES_DIR, MUSIC_DIR):
        path.mkdir(parents=True, exist_ok=True)


def image_provider_ready(provider: str | None = None) -> bool:
    provider = (provider or IMAGE_PROVIDER).lower()
    return {
        "gemini": bool(GEMINI_API_KEY),
        "fal": bool(FAL_KEY),
        "openai": bool(OPENAI_API_KEY),
    }.get(provider, False)


def tts_provider_ready(provider: str | None = None) -> bool:
    provider = (provider or TTS_PROVIDER).lower()
    return {
        "elevenlabs": bool(ELEVENLABS_API_KEY),
        "openai": bool(OPENAI_API_KEY),
        "gemini": bool(GEMINI_API_KEY),
        "upload": True,
    }.get(provider, False)


def resolve_align_provider(tts_provider: str) -> str:
    """Pick the best subtitle-timing source available for this TTS provider."""
    if ALIGN_PROVIDER != "auto":
        return ALIGN_PROVIDER
    if tts_provider == "elevenlabs" and ELEVENLABS_API_KEY:
        return "elevenlabs"
    if OPENAI_API_KEY:
        return "whisper"
    return "estimate"
