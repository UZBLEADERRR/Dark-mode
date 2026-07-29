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
DB_PATH = DATA_DIR / "app.db"

# Optional Postgres, used for the hero library alone. Railway injects
# DATABASE_URL when a Postgres service is attached; without it everything stays
# in SQLite. Heroes are the only uploads a user cannot recreate, so they are the
# only thing worth keeping off a filesystem that a deploy wipes.
DATABASE_URL = _env("DATABASE_URL", "").strip()

# Hero photos and music live in SQLite as blobs, not loose files — one database
# file is the whole library, so a single Railway volume keeps everything.
STORAGE_BACKEND = _env("STORAGE_BACKEND", "local").lower()  # local | supabase
SUPABASE_URL = _env("SUPABASE_URL").rstrip("/")
SUPABASE_SERVICE_KEY = _env("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = _env("SUPABASE_BUCKET", "videos")

# --- LLM ---------------------------------------------------------------------
# auto -> Claude when ANTHROPIC_API_KEY is set, otherwise Gemini. With only a
# Gemini key configured the whole app (script, prompts, subtitles, images,
# voice) runs on that single key.
LLM_PROVIDER = _env("LLM_PROVIDER", "auto").lower()
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
LLM_MODEL = _env("LLM_MODEL", "claude-opus-5")
LLM_EFFORT = _env("LLM_EFFORT", "high")
GEMINI_TEXT_MODEL = _env("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
GEMINI_TEXT_FALLBACK = _env("GEMINI_TEXT_FALLBACK", "gemini-2.5-flash")

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

GEMINI_TTS_MODEL = _env("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview")
# A preview model can vanish or be missing from a given key, and unlike the
# script stage the voice had nowhere to fall back to.
GEMINI_TTS_FALLBACK = _env("GEMINI_TTS_FALLBACK", "gemini-2.5-flash-preview-tts")
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

# How long one provider call may take, and how long all of its retries may take
# together. The second number is the one that matters: three retries behind a
# generous per-call timeout used to mean a single stalled scene could hold the
# whole stage for ten minutes with nothing on screen to say so.
TTS_TIMEOUT = float(_env("TTS_TIMEOUT", "90"))
TTS_DEADLINE = float(_env("TTS_DEADLINE", "180"))

# Pacing ourselves beats being throttled: a refused call still costs a round
# trip, and its retry lands in the same full window. A 429 that arrives anyway
# is waited out rather than counted as a failure, and TTS_RATE_PATIENCE caps how
# long one line may spend queueing.
#
# The number is per provider, because the providers are not alike. Gemini's free
# tier sells voice by the minute; ElevenLabs and OpenAI limit how many calls run
# at once, not how many start, so pacing them only makes a long video slower for
# no reason. One shared ceiling meant connecting ElevenLabs to escape Gemini's
# limit left you queueing behind Gemini's limit anyway.
_TTS_RATE_DEFAULTS = {"gemini": 10, "elevenlabs": 0, "openai": 0}
TTS_RATE_LIMIT = _int("TTS_RATE_LIMIT", _TTS_RATE_DEFAULTS["gemini"])
_TTS_RATE_FOR_ALL = bool(_env("TTS_RATE_LIMIT"))
TTS_RATE_PATIENCE = float(_env("TTS_RATE_PATIENCE", "900"))


def tts_rate_limit(provider: str) -> int:
    """Calls a minute for this provider. 0 means no pacing at all.

    `TTS_RATE_LIMIT_<PROVIDER>` is the specific answer; a bare `TTS_RATE_LIMIT`
    still applies to everything, so the old single knob keeps working for anyone
    who set it deliberately.
    """
    provider = (provider or "").lower()
    specific = _env(f"TTS_RATE_LIMIT_{provider.upper()}")
    if specific:
        try:
            return max(0, int(specific))
        except ValueError:
            pass
    if _TTS_RATE_FOR_ALL:
        return TTS_RATE_LIMIT
    return _TTS_RATE_DEFAULTS.get(provider, 0)
IMAGE_TIMEOUT = float(_env("IMAGE_TIMEOUT", "150"))
IMAGE_DEADLINE = float(_env("IMAGE_DEADLINE", "330"))
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


# --- which model each stage calls --------------------------------------------
# The environment sets the default; the database can override it while the app
# is running, so a new model can be adopted from the UI without a redeploy.
# Nothing reads the constants above directly any more — everything goes through
# `model()`, or an override would apply in some code paths and not others.

MODEL_STAGES: dict[str, dict] = {
    "gemini_text":       {"provider": "gemini", "role": "text"},
    "gemini_text_fallback": {"provider": "gemini", "role": "text"},
    "gemini_image":      {"provider": "gemini", "role": "image"},
    "gemini_tts":        {"provider": "gemini", "role": "tts"},
    "gemini_tts_fallback": {"provider": "gemini", "role": "tts"},
    "anthropic_text":    {"provider": "anthropic", "role": "text"},
    "fal_image":         {"provider": "fal", "role": "image"},
    "fal_text2img":      {"provider": "fal", "role": "image"},
    "openai_image":      {"provider": "openai", "role": "image"},
    "openai_tts":        {"provider": "openai", "role": "tts"},
    "openai_transcribe": {"provider": "openai", "role": "transcribe"},
    "elevenlabs_tts":    {"provider": "elevenlabs", "role": "tts"},
}

_MODEL_DEFAULTS: dict[str, str] = {
    "gemini_text": GEMINI_TEXT_MODEL,
    "gemini_text_fallback": GEMINI_TEXT_FALLBACK,
    "gemini_image": GEMINI_IMAGE_MODEL,
    "gemini_tts": GEMINI_TTS_MODEL,
    "gemini_tts_fallback": GEMINI_TTS_FALLBACK,
    "anthropic_text": LLM_MODEL,
    "fal_image": FAL_IMAGE_MODEL,
    "fal_text2img": FAL_TEXT2IMG_MODEL,
    "openai_image": OPENAI_IMAGE_MODEL,
    "openai_tts": OPENAI_TTS_MODEL,
    "openai_transcribe": OPENAI_TRANSCRIBE_MODEL,
    "elevenlabs_tts": ELEVENLABS_MODEL,
}

MODEL_OVERRIDES: dict[str, str] = {}


def model(key: str) -> str:
    return (MODEL_OVERRIDES.get(key) or _MODEL_DEFAULTS.get(key, "")).strip()


def model_defaults() -> dict[str, str]:
    return dict(_MODEL_DEFAULTS)


def set_model_overrides(values: dict | None) -> dict[str, str]:
    """Replace the overrides wholesale. An empty value means "use the default"."""
    MODEL_OVERRIDES.clear()
    for key, value in (values or {}).items():
        if key in _MODEL_DEFAULTS and str(value or "").strip():
            MODEL_OVERRIDES[key] = str(value).strip()[:120]
    return dict(MODEL_OVERRIDES)


# --- default voices ----------------------------------------------------------

_VOICE_DEFAULTS: dict[str, str] = {
    "gemini": GEMINI_TTS_VOICE,
    "openai": OPENAI_TTS_VOICE,
    "elevenlabs": ELEVENLABS_VOICE_ID,
}

VOICE_OVERRIDES: dict[str, str] = {}


def default_voice(provider: str) -> str:
    return (VOICE_OVERRIDES.get(provider) or _VOICE_DEFAULTS.get(provider, "")).strip()


def set_voice_overrides(values: dict | None) -> dict[str, str]:
    VOICE_OVERRIDES.clear()
    for key, value in (values or {}).items():
        if key in _VOICE_DEFAULTS and str(value or "").strip():
            VOICE_OVERRIDES[key] = str(value).strip()[:120]
    return dict(VOICE_OVERRIDES)


# --- render speed ------------------------------------------------------------
# Rendering encodes twice: once per scene into a clip, then once more to
# cross-fade them together. Every knob that matters for wall-clock time is
# collected here so one choice moves all of them coherently.

import os as _os

CPU_COUNT = max(1, _os.cpu_count() or 2)
RENDER_SPEED = _env("RENDER_SPEED", "balanced").lower()

SPEED_PROFILES: dict[str, dict] = {
    "fast": {
        "label": "Tez",
        "clip_preset": "ultrafast", "clip_crf": 14,
        "final_preset": "veryfast", "final_crf": 22,
        # The still is scaled above the canvas so a zoom downsamples rather than
        # stretches. Less headroom is softer under a hard zoom, but zoompan cost
        # scales with the square of this number, so it is the biggest lever.
        "supersample": 1.35,
    },
    "balanced": {
        "label": "Muvozanat",
        "clip_preset": "veryfast", "clip_crf": 16,
        "final_preset": "faster", "final_crf": 20,
        "supersample": 1.7,
    },
    "quality": {
        "label": "Sifat",
        "clip_preset": "veryfast", "clip_crf": 14,
        "final_preset": "medium", "final_crf": 18,
        "supersample": 2.0,
    },
}


def speed_profile(name: str | None = None) -> dict:
    profile = SPEED_PROFILES.get((name or RENDER_SPEED).lower())
    if profile is None:
        profile = SPEED_PROFILES["balanced"]
    # Clips run several at a time, but each ffmpeg also threads internally, so
    # the two have to be divided rather than both given the whole machine —
    # oversubscribing costs more in context switching than it wins in overlap.
    workers = max(2, min(CPU_COUNT, 4))
    return {**profile, "workers": workers, "threads": max(1, CPU_COUNT // workers)}


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
    for path in (DATA_DIR, PROJECTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def llm_provider() -> str:
    """Which model writes the script. Falls back to whichever key exists."""
    if LLM_PROVIDER in {"anthropic", "claude"}:
        return "anthropic"
    if LLM_PROVIDER == "gemini":
        return "gemini"
    return "anthropic" if ANTHROPIC_API_KEY else "gemini"


def llm_ready() -> bool:
    return bool(ANTHROPIC_API_KEY) if llm_provider() == "anthropic" else bool(GEMINI_API_KEY)


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
