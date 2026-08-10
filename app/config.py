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

# --- YouTube -----------------------------------------------------------------
# Publishing is OAuth, not an API key: the app acts as the user's own channel, so
# it needs their consent and a refresh token — which is stored in the database
# rather than the environment, because the person who grants it is not the person
# who deploys the app.
YOUTUBE_CLIENT_ID = _env("YOUTUBE_CLIENT_ID")
YOUTUBE_CLIENT_SECRET = _env("YOUTUBE_CLIENT_SECRET")
# Where Google sends the browser back to. Railway sets RAILWAY_PUBLIC_DOMAIN, so
# a normal deployment needs no extra variable; anything else can say so directly.
PUBLIC_URL = (_env("PUBLIC_URL")
              or (f"https://{_env('RAILWAY_PUBLIC_DOMAIN')}"
                  if _env("RAILWAY_PUBLIC_DOMAIN") else "")).rstrip("/")


def youtube_ready() -> bool:
    """Whether the app *could* publish — a channel still has to be connected."""
    return bool(YOUTUBE_CLIENT_ID and YOUTUBE_CLIENT_SECRET)

# --- LLM ---------------------------------------------------------------------
# auto -> Claude when ANTHROPIC_API_KEY is set, otherwise Gemini. With only a
# Gemini key configured the whole app (script, prompts, subtitles, images,
# voice) runs on that single key.
LLM_PROVIDER = _env("LLM_PROVIDER", "auto").lower()
# What the environment asked for, kept apart from what is in force — switching
# from the app has to be reversible without knowing what it was before.
LLM_PROVIDER_ENV = LLM_PROVIDER
ANTHROPIC_API_KEY = _env("ANTHROPIC_API_KEY")
LLM_MODEL = _env("LLM_MODEL", "claude-opus-5")
LLM_EFFORT = _env("LLM_EFFORT", "high")
GEMINI_TEXT_MODEL = _env("GEMINI_TEXT_MODEL", "gemini-3.1-pro-preview")
GEMINI_TEXT_FALLBACK = _env("GEMINI_TEXT_FALLBACK", "gemini-2.5-flash")

# How long to wait on the model that writes, and how long before deciding it is
# not going to answer at all.
#
# These are two different numbers on purpose. The preferred model is a reasoning
# one: it can think for minutes before it writes a word, and a script is the
# largest thing it is ever asked for. The fallback is a fast model that answers
# in seconds. Waiting the full patience on the slow one before even trying the
# fast one is five minutes of a progress bar not moving — which is what it did.
# So the first model gets a shorter leash and the fallback gets the full one:
# the worst case becomes "two minutes, then an answer" instead of "five minutes,
# then five more".
LLM_TIMEOUT = float(_env("LLM_TIMEOUT", "300"))
LLM_FIRST_WAIT = float(_env("LLM_FIRST_WAIT", "120"))

# --- image generation --------------------------------------------------------
# gemini | fal | openai | flow | flowagent | manual
IMAGE_PROVIDER = _env("IMAGE_PROVIDER", "gemini").lower()
# What the environment asked for, kept apart from what is in force. Switching to
# `flow` from the app has to be reversible without knowing what it was before —
# and "before" is this, not whatever was last chosen.
IMAGE_PROVIDER_ENV = IMAGE_PROVIDER
IMAGE_PROVIDERS = ("gemini", "fal", "openai", "flow", "flowagent", "manual")

# Flow Agent — the same Google Flow subscription, reached through its own backend
# instead of through a browser this app is driving. `flow` parks a prompt and
# waits for somebody's browser to answer it; `flowagent` asks for a picture and
# is given one, which is the difference between a queue and a provider.
FLOW_AGENT_URL = _env("FLOW_AGENT_URL", "http://localhost:8001").rstrip("/")
FLOW_AGENT_KEY = _env("FLOW_AGENT_KEY")
FLOW_AGENT_MODEL = _env("FLOW_AGENT_MODEL", "gem_pix_2")
# Its bridge waits on a browser at the other end of a WebSocket, so a call can
# sit for a while before anything starts happening.
FLOW_AGENT_TIMEOUT = _int("FLOW_AGENT_TIMEOUT", 300)

GEMINI_API_KEY = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
GEMINI_IMAGE_MODEL = _env("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")
GEMINI_BASE = _env("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")

FAL_KEY = _env("FAL_KEY")
FAL_IMAGE_MODEL = _env("FAL_IMAGE_MODEL", "fal-ai/flux-pro/kontext/max/multi")
FAL_TEXT2IMG_MODEL = _env("FAL_TEXT2IMG_MODEL", "fal-ai/flux/dev")

OPENAI_API_KEY = _env("OPENAI_API_KEY")
OPENAI_BASE = _env("OPENAI_BASE", "https://api.openai.com/v1")
OPENAI_IMAGE_MODEL = _env("OPENAI_IMAGE_MODEL", "gpt-image-1")
OPENAI_TEXT_MODEL = _env("OPENAI_TEXT_MODEL", "gpt-5")

# --- text to speech ----------------------------------------------------------
TTS_PROVIDER = _env("TTS_PROVIDER", "elevenlabs").lower()
# `edge` is Microsoft Edge's read-aloud service: no account, no key, a man and a
# woman in most languages. Listed last because it is not a published API and
# should be a choice somebody makes, not a default they inherit.
TTS_PROVIDERS = ("elevenlabs", "openai", "gemini", "edge")

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

# The free narrator's default. Uzbek, because that is what this app is written
# in and what most of its videos are narrated in; every other language is one
# choice away in the picker.
EDGE_TTS_VOICE = _env("EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")

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

# DejaVu has no Hangul, and a missing glyph is not an error — libass draws
# nothing and the subtitle comes out blank. So a language whose script the house
# font cannot draw names its own font, and the image installs it.
SCRIPT_FONTS: dict[str, str] = {
    "ko": _env("SUBTITLE_FONT_KO", "NanumGothic"),
}


def subtitle_font(language: str = "") -> str:
    """The font to set captions in for this language.

    A style that names its own font still wins — this is the default, not a
    rule.
    """
    return SCRIPT_FONTS.get((language or "").lower(), SUBTITLE_FONT)

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

# Reading a whole passage in one request rather than a line at a time. Only
# ElevenLabs can do this honestly: it returns a timing for every character, so
# the recording can be cut back into scenes at the exact place each line ends.
# Fifty-eight requests become three, and the narrator carries its intonation
# across sentences instead of restarting at every full stop.
TTS_BATCH = _flag("TTS_BATCH", True)
TTS_BATCH_CHARS = _int("TTS_BATCH_CHARS", 4200)   # provider cap is ~5000
TTS_BATCH_LINES = _int("TTS_BATCH_LINES", 12)


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
# How long a scene will wait for a picture that is being made somewhere else.
# Nothing like the deadline above: that one bounds an API call, this one bounds a
# browser queue and, at the far end of it, possibly a person. Twenty minutes is
# long enough to open the laptop and let the extension work through a backlog,
# and short enough that a render does not sit there overnight.
FLOW_PATIENCE = float(_env("FLOW_PATIENCE", "1200"))
FLOW_POLL_SECONDS = float(_env("FLOW_POLL_SECONDS", "2"))
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
    "ko": "Korean (한국어)",
}

# Languages written without spaces between words, in characters that are about
# twice as wide as a Latin letter. Both facts change how a caption is broken, so
# they are named once here rather than guessed at each place that breaks one.
DENSE_SCRIPTS = {"ko", "ja", "zh"}


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
    "openai_text":       {"provider": "openai", "role": "text"},
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
    "openai_text": OPENAI_TEXT_MODEL,
    "openai_image": OPENAI_IMAGE_MODEL,
    "openai_tts": OPENAI_TTS_MODEL,
    "openai_transcribe": OPENAI_TRANSCRIBE_MODEL,
    "elevenlabs_tts": ELEVENLABS_MODEL,
}

MODEL_OVERRIDES: dict[str, str] = {}


def model(key: str) -> str:
    return (MODEL_OVERRIDES.get(key) or _MODEL_DEFAULTS.get(key, "")).strip()


def set_image_provider(name: str | None) -> str:
    """Change which provider draws the scenes, for every video from now on.

    Reassigns the module attribute rather than hiding behind a getter, because
    every reader already says `config.IMAGE_PROVIDER` at the moment it needs it —
    so this is one line here instead of a rename in a dozen places. An empty name
    means "back to whatever the environment said".
    """
    global IMAGE_PROVIDER
    wanted = (name or "").strip().lower()
    IMAGE_PROVIDER = wanted if wanted in IMAGE_PROVIDERS else IMAGE_PROVIDER_ENV
    return IMAGE_PROVIDER


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
    "edge": EDGE_TTS_VOICE,
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


def _cpu_allowance() -> int:
    """How many cores this container may actually use.

    `os.cpu_count()` reports the *machine*, and a container on a shared host is
    told about all of them — thirty-two on a box where the plan allows eight.
    Every thread count derived from that number is then four times too large,
    and the kernel answers by throttling: the work does not go faster, it goes
    slower, in stalls, while each extra ffmpeg thread still costs its buffers.

    The quota is what the scheduler enforces, so the quota is what is asked.
    """
    for quota_file, period_file in (("cpu.max", None),
                                    ("cpu/cpu.cfs_quota_us", "cpu/cpu.cfs_period_us")):
        try:
            raw = (Path("/sys/fs/cgroup") / quota_file).read_text().split()
            if period_file is None:  # cgroup v2: "<quota> <period>", or "max ..."
                quota, period = raw[0], raw[1]
            else:
                quota = raw[0]
                period = (Path("/sys/fs/cgroup") / period_file).read_text().strip()
            if quota in ("max", "-1"):
                continue
            cores = int(quota) / int(period)
            if cores >= 0.5:
                return max(1, int(cores))
        except (OSError, ValueError, IndexError):
            continue
    return max(1, _os.cpu_count() or 2)


def _memory_allowance() -> int:
    """How many bytes this container may use, or 0 when nothing says.

    The same problem as the cores: the machine has plenty and the plan does not.
    Read so the render can size itself to the box it is actually on rather than
    to the one it can see.
    """
    for name in ("memory.max", "memory/memory.limit_in_bytes"):
        try:
            raw = (Path("/sys/fs/cgroup") / name).read_text().strip()
            if raw in ("max", ""):
                continue
            limit = int(raw)
            # cgroup v1 reports a number near 2^63 for "no limit".
            if 0 < limit < 1 << 50:
                return limit
        except (OSError, ValueError):
            continue
    return 0


CPU_COUNT = _int("CPU_COUNT", _cpu_allowance())
MEMORY_LIMIT = _int("MEMORY_LIMIT", _memory_allowance())
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
        "fuse_preset": "superfast", "fuse_crf": 21,
        "fuse_maxrate": "9M", "fuse_bufsize": "18M",
    },
    "balanced": {
        "label": "Muvozanat",
        "clip_preset": "veryfast", "clip_crf": 16,
        "final_preset": "faster", "final_crf": 20,
        "supersample": 1.7,
        # What a half-joined batch is written at. See `_fuse_group`: this file is
        # read once by the final encode and deleted, so what matters is that the
        # last pass cannot see the difference — not that it is pristine.
        "fuse_preset": "superfast", "fuse_crf": 20,
        "fuse_maxrate": "12M", "fuse_bufsize": "24M",
    },
    "quality": {
        "label": "Sifat",
        "clip_preset": "veryfast", "clip_crf": 14,
        "final_preset": "medium", "final_crf": 18,
        "supersample": 2.0,
        "fuse_preset": "veryfast", "fuse_crf": 18,
        "fuse_maxrate": "16M", "fuse_bufsize": "32M",
    },
}


def speed_profile(name: str | None = None) -> dict:
    profile = SPEED_PROFILES.get((name or RENDER_SPEED).lower())
    if profile is None:
        profile = SPEED_PROFILES["balanced"]
    # Clips run several at a time, but each ffmpeg also threads internally, so
    # the two have to be divided rather than both given the whole machine —
    # oversubscribing costs more in context switching than it wins in overlap.
    # Up to eight, not four. Measured: four scenes animate in the time one does
    # (4.1x on a four-core box), because the Ken Burns filter is one thread and
    # the work is spread across processes rather than inside them. Capping at
    # four therefore left half of an eight-core plan idle and made a
    # ninety-scene render take twice as long as the box could do it in.
    workers = max(1, min(CPU_COUNT, 8))
    if MEMORY_LIMIT:
        # Each 1080p encoder holds a decoded frame, a supersampled copy and the
        # zoompan buffer at once. Measured at 221 MB, and — worth knowing — flat
        # in the length of the scene: a sixty-second scene costs the same as a
        # four-second one. Half a gigabyte each leaves room for everything else
        # the box is doing.
        workers = max(1, min(workers, int(MEMORY_LIMIT / (512 * 1024 * 1024))))
    workers = max(1, _int("RENDER_WORKERS", workers))
    return {**profile, "workers": workers, "threads": max(1, CPU_COUNT // workers)}


def caption_budget(width: int, height: int,
                   language: str = "") -> dict[str, int | float]:
    """How wide a caption line may be, for this canvas and this script.

    A 9:16 frame is 1080px across where 16:9 is 1920, so a line length tuned for
    landscape runs straight off the edge of a Short. Both the line-breaking skill
    and the ASS font size derive from this one budget so they can never disagree.

    Korean, Japanese and Chinese need their own answer: a Hangul syllable paints
    about twice the width of a Latin letter, so a line of forty-two characters
    that fits in English runs past both edges in Korean. Measured, not guessed —
    thirty characters of Korean already paint three quarters of a 1920 frame.
    """
    ratio = width / max(height, 1)
    if ratio >= 1.3:            # 16:9 and wider
        max_chars, max_words, margin = 42, 7, 0.08
    elif ratio >= 0.9:          # square
        max_chars, max_words, margin = 30, 5, 0.07
    else:                       # 9:16, 4:5 and other portrait
        max_chars, max_words, margin = 24, 4, 0.06

    # A Hangul syllable is close to a full em wide where a Latin letter averages
    # a little over half of one, so the same line runs almost twice as far. The
    # word cap goes up rather than down: Korean words are short, and holding a
    # line to seven of them would break it long before the width ran out.
    dense = (language or "").lower() in DENSE_SCRIPTS
    advance = 0.55
    if dense:
        max_chars = max(8, int(max_chars * 0.55))
        max_words = max_words + 3
        advance = 1.0

    usable = width * (1 - 2 * margin)
    # DejaVu Sans Bold averages ~0.55em of advance per character; a CJK face is
    # square, so one character is one em.
    font_size = int(usable / (max_chars * advance))
    return {
        "max_chars": max_chars,
        "max_words": max_words,
        "margin": margin,
        "font_size": max(20, min(font_size, int(height / 9))),
    }


def ensure_dirs() -> None:
    for path in (DATA_DIR, PROJECTS_DIR):
        path.mkdir(parents=True, exist_ok=True)


# --- where a key comes from ---------------------------------------------------
# The keyring (app/keys.py) installs itself here at import. Config stays free of
# imports from the rest of the app — it is read by everything, including the
# keyring — so the direction is inverted rather than the dependency added.

_key_for = None            # (provider) -> secret to use now
_key_count = None          # (provider) -> how many keys exist at all


def set_key_source(pick, count) -> None:
    global _key_for, _key_count
    _key_for, _key_count = pick, count


_ENV_KEYS = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "fal": "FAL_KEY",
}


def env_key(provider: str) -> str:
    return globals().get(_ENV_KEYS.get(provider, ""), "") or ""


def key(provider: str) -> str:
    """The secret to use for the next call to this provider.

    Read at call time, never captured: that is what lets a retry land on a
    different key than the attempt before it.
    """
    if _key_for is not None:
        picked = _key_for(provider)
        if picked:
            return picked
    return env_key(provider)


def has_key(provider: str) -> bool:
    """Whether this provider can be used at all — not whether it is free now.

    A key that is cooling off after a rate limit still counts: the provider is
    configured, and reporting it as missing would turn a busy minute into "you
    have not set this up".
    """
    if _key_count is not None:
        return _key_count(provider) > 0
    return bool(env_key(provider))


# Which providers can write a script, in the order `auto` prefers them when more
# than one has a key. Order is a judgement about prose, not about price.
LLM_PROVIDERS = ("anthropic", "openai", "gemini")


def llm_provider() -> str:
    """Which model writes the script. Falls back to whichever key exists."""
    if LLM_PROVIDER in {"anthropic", "claude"}:
        return "anthropic"
    if LLM_PROVIDER in {"openai", "gpt", "chatgpt"}:
        return "openai"
    if LLM_PROVIDER == "gemini":
        return "gemini"
    # `auto`: the first one that can actually be called. Gemini is last because
    # it is the one every deployment already has a key for, so naming it first
    # would mean a stored OpenAI key never got used.
    return next((name for name in LLM_PROVIDERS if has_key(name)), "gemini")


def llm_ready() -> bool:
    return has_key(llm_provider())


def set_llm_provider(name: str | None) -> str:
    """Change which model writes the script, for every video from now on.

    Same shape as `set_image_provider`: the module attribute is reassigned
    because every reader already asks for it at the moment it needs it. An empty
    name means "back to whatever the environment said".
    """
    global LLM_PROVIDER
    wanted = (name or "").strip().lower()
    LLM_PROVIDER = wanted if wanted in (*LLM_PROVIDERS, "auto") else LLM_PROVIDER_ENV
    return llm_provider()


def image_provider_ready(provider: str | None = None) -> bool:
    provider = (provider or IMAGE_PROVIDER).lower()
    # `flow` has no key to check and never will: the pictures are made in a
    # browser that is already signed in to Google, and this app never sees the
    # account. It is ready as soon as it is chosen — whether anything is actually
    # listening for the prompts is answered by the queue, not by a key.
    if provider == "flow":
        return True
    # Flow Agent is the same story with an address instead of a queue: no key of
    # ours is involved, and whether its browser is connected is a question for it,
    # not for this function.
    if provider == "flowagent":
        return bool(FLOW_AGENT_URL)
    # `manual` draws nothing at all: the draft stops at the pictures and hands
    # over the prompts, and the pictures come back as an upload. There is nothing
    # it could fail to be ready for.
    if provider == "manual":
        return True
    return has_key(provider) if provider in {"gemini", "fal", "openai"} else False


def tts_provider_ready(provider: str | None = None) -> bool:
    provider = (provider or TTS_PROVIDER).lower()
    if provider == "upload":
        return True
    # Edge's read-aloud service wants no account and no key. Whether it will
    # answer today is a question for the request, not for a key check — the same
    # arrangement `flow` has on the picture side.
    if provider == "edge":
        return True
    return has_key(provider) if provider in {"elevenlabs", "openai", "gemini"} else False


def resolve_align_provider(tts_provider: str) -> str:
    """Pick the best subtitle-timing source available for this TTS provider."""
    if ALIGN_PROVIDER != "auto":
        return ALIGN_PROVIDER
    if tts_provider == "elevenlabs" and has_key("elevenlabs"):
        return "elevenlabs"
    if has_key("openai"):
        return "whisper"
    return "estimate"
