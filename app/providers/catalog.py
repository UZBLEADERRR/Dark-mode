"""What each provider can actually do, asked at runtime rather than guessed.

Two lists matter to someone setting the app up:

* **models** — every provider ships new ones and retires old ones on its own
  schedule, so the only list that stays right is the one the provider returns
  for *your* key. Where a provider has no list endpoint, the field stays free
  text rather than a menu of names that will rot.
* **voices** — ElevenLabs has an API for this. Gemini and OpenAI publish fixed
  sets, so those are held here, with a spoken sample available for every one of
  them: a name like "Fenrir" tells you nothing until you hear it.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx

from .. import config
from . import tts

TIMEOUT = httpx.Timeout(30.0, connect=10.0)

# The line every voice reads in the preview. Short enough to be cheap, long
# enough to hear the timbre settle.
SAMPLES: dict[str, str] = {
    "en": "This is how your video will sound. Every scene, in this voice.",
    "uz": "Videongiz shunday ovozda chiqadi. Har bir sahna shu ovoz bilan.",
    "ru": "Вот как будет звучать ваше видео. Каждая сцена этим голосом.",
    "tr": "Videonuz böyle duyulacak. Her sahne bu sesle.",
    "es": "Así sonará tu vídeo. Cada escena, con esta voz.",
    "ar": "هكذا سيبدو الفيديو الخاص بك. كل مشهد بهذا الصوت.",
    "hi": "आपका वीडियो ऐसा सुनाई देगा। हर दृश्य इसी आवाज़ में।",
    "de": "So wird dein Video klingen. Jede Szene mit dieser Stimme.",
    "fr": "Voici comment votre vidéo va sonner. Chaque scène, avec cette voix.",
}

# Gemini's prebuilt voices. Google documents each by character rather than by
# gender, so that is what is recorded here; the `hint` is a rough read to narrow
# the list down, and the preview settles it.
GEMINI_VOICES: list[dict] = [
    {"id": "Zephyr", "label": "Zephyr", "tone": "Yorqin", "hint": "ayol"},
    {"id": "Puck", "label": "Puck", "tone": "Ko'tarinki", "hint": "erkak"},
    {"id": "Charon", "label": "Charon", "tone": "Ma'lumot beruvchi", "hint": "erkak"},
    {"id": "Kore", "label": "Kore", "tone": "Qat'iy", "hint": "ayol"},
    {"id": "Fenrir", "label": "Fenrir", "tone": "Hayajonli", "hint": "erkak"},
    {"id": "Leda", "label": "Leda", "tone": "Yoshlarcha", "hint": "ayol"},
    {"id": "Orus", "label": "Orus", "tone": "Qat'iy", "hint": "erkak"},
    {"id": "Aoede", "label": "Aoede", "tone": "Yengil", "hint": "ayol"},
    {"id": "Callirrhoe", "label": "Callirrhoe", "tone": "Bosiq", "hint": "ayol"},
    {"id": "Autonoe", "label": "Autonoe", "tone": "Yorqin", "hint": "ayol"},
    {"id": "Enceladus", "label": "Enceladus", "tone": "Nafasli", "hint": "erkak"},
    {"id": "Iapetus", "label": "Iapetus", "tone": "Tiniq", "hint": "erkak"},
    {"id": "Umbriel", "label": "Umbriel", "tone": "Bosiq", "hint": "erkak"},
    {"id": "Algieba", "label": "Algieba", "tone": "Silliq", "hint": "erkak"},
    {"id": "Despina", "label": "Despina", "tone": "Silliq", "hint": "ayol"},
    {"id": "Erinome", "label": "Erinome", "tone": "Tiniq", "hint": "ayol"},
    {"id": "Algenib", "label": "Algenib", "tone": "Xirqiroq", "hint": "erkak"},
    {"id": "Rasalgethi", "label": "Rasalgethi", "tone": "Ma'lumot beruvchi", "hint": "erkak"},
    {"id": "Laomedeia", "label": "Laomedeia", "tone": "Ko'tarinki", "hint": "ayol"},
    {"id": "Achernar", "label": "Achernar", "tone": "Yumshoq", "hint": "ayol"},
    {"id": "Alnilam", "label": "Alnilam", "tone": "Qat'iy", "hint": "erkak"},
    {"id": "Schedar", "label": "Schedar", "tone": "Bir tekis", "hint": "erkak"},
    {"id": "Gacrux", "label": "Gacrux", "tone": "Kattalarcha", "hint": "ayol"},
    {"id": "Pulcherrima", "label": "Pulcherrima", "tone": "Oldinga suruvchi", "hint": "ayol"},
    {"id": "Achird", "label": "Achird", "tone": "Do'stona", "hint": "erkak"},
    {"id": "Zubenelgenubi", "label": "Zubenelgenubi", "tone": "Kundalik", "hint": "erkak"},
    {"id": "Vindemiatrix", "label": "Vindemiatrix", "tone": "Muloyim", "hint": "ayol"},
    {"id": "Sadachbia", "label": "Sadachbia", "tone": "Jonli", "hint": "erkak"},
    {"id": "Sadaltager", "label": "Sadaltager", "tone": "Bilimdon", "hint": "erkak"},
    {"id": "Sulafat", "label": "Sulafat", "tone": "Iliq", "hint": "ayol"},
]

OPENAI_VOICES: list[dict] = [
    {"id": "alloy", "label": "Alloy", "tone": "Neytral", "hint": "erkak"},
    {"id": "ash", "label": "Ash", "tone": "Bosiq", "hint": "erkak"},
    {"id": "ballad", "label": "Ballad", "tone": "Hissiy", "hint": "erkak"},
    {"id": "coral", "label": "Coral", "tone": "Iliq", "hint": "ayol"},
    {"id": "echo", "label": "Echo", "tone": "Tiniq", "hint": "erkak"},
    {"id": "fable", "label": "Fable", "tone": "Hikoyachi", "hint": "erkak"},
    {"id": "nova", "label": "Nova", "tone": "Yorqin", "hint": "ayol"},
    {"id": "onyx", "label": "Onyx", "tone": "Chuqur", "hint": "erkak"},
    {"id": "sage", "label": "Sage", "tone": "Tinch", "hint": "ayol"},
    {"id": "shimmer", "label": "Shimmer", "tone": "Yengil", "hint": "ayol"},
    {"id": "verse", "label": "Verse", "tone": "Ifodali", "hint": "erkak"},
]


# ── models ────────────────────────────────────────────────────────────────────

def _role_of(name: str) -> str:
    lowered = name.lower()
    if "tts" in lowered or "speech" in lowered:
        return "tts"
    if "image" in lowered or "imagen" in lowered or "dall" in lowered:
        return "image"
    if "embedding" in lowered or "embed" in lowered:
        return "embedding"
    if "whisper" in lowered or "transcribe" in lowered:
        return "transcribe"
    return "text"


async def list_models(provider: str) -> dict:
    """Ask a provider what models this key may call. Never raises."""
    provider = (provider or "").lower()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if provider == "gemini":
                secret = config.key("gemini")
                if not secret:
                    return {"provider": provider, "models": [], "error": "Gemini kaliti yo'q — kutubxonaga qo'shing yoki GEMINI_API_KEY ni sozlang"}
                resp = await client.get(
                    f"{config.GEMINI_BASE}/models?pageSize=200",
                    headers={"x-goog-api-key": secret},
                )
                resp.raise_for_status()
                out = []
                for entry in resp.json().get("models", []):
                    name = str(entry.get("name", "")).removeprefix("models/")
                    if not name:
                        continue
                    out.append({
                        "id": name,
                        "label": entry.get("displayName") or name,
                        "role": _role_of(name),
                    })
                return {"provider": provider, "models": out}

            if provider == "openai":
                secret = config.key("openai")
                if not secret:
                    return {"provider": provider, "models": [], "error": "OpenAI kaliti yo'q — kutubxonaga qo'shing yoki OPENAI_API_KEY ni sozlang"}
                resp = await client.get(
                    f"{config.OPENAI_BASE}/models",
                    headers={"Authorization": f"Bearer {secret}"},
                )
                resp.raise_for_status()
                return {"provider": provider, "models": [
                    {"id": m["id"], "label": m["id"], "role": _role_of(m["id"])}
                    for m in resp.json().get("data", []) if m.get("id")
                ]}

            if provider == "elevenlabs":
                secret = config.key("elevenlabs")
                if not secret:
                    return {"provider": provider, "models": [], "error": "ElevenLabs kaliti yo'q — kutubxonaga qo'shing yoki ELEVENLABS_API_KEY ni sozlang"}
                resp = await client.get(
                    "https://api.elevenlabs.io/v1/models",
                    headers={"xi-api-key": secret},
                )
                resp.raise_for_status()
                return {"provider": provider, "models": [
                    {"id": m["model_id"], "label": m.get("name") or m["model_id"], "role": "tts"}
                    for m in resp.json() if m.get("model_id") and m.get("can_do_text_to_speech", True)
                ]}

        # fal publishes no listing endpoint for a key, so the field stays open.
        return {"provider": provider, "models": [],
                "error": "Bu provayderda ro'yxat API'si yo'q — nomini qo'lda yozing"}
    except Exception as exc:  # noqa: BLE001 - a listing failure must not break the page
        return {"provider": provider, "models": [], "error": str(exc)[:200]}


# ── voices ────────────────────────────────────────────────────────────────────

async def list_voices(provider: str) -> dict:
    provider = (provider or config.TTS_PROVIDER).lower()

    if provider == "gemini":
        return {"provider": provider, "voices": GEMINI_VOICES,
                "default": config.default_voice("gemini")}
    if provider == "openai":
        return {"provider": provider, "voices": OPENAI_VOICES,
                "default": config.default_voice("openai")}

    if provider == "elevenlabs":
        if not config.has_key("elevenlabs"):
            return {"provider": provider, "voices": [], "default": "",
                    "error": "ElevenLabs kaliti yo'q — kutubxonaga qo'shing "
                             "yoki ELEVENLABS_API_KEY ni sozlang"}
        try:
            entries, source = await _elevenlabs_voices()
            voices = []
            for entry in entries:
                labels = entry.get("labels") or {}
                tone = ", ".join(
                    str(labels[k]) for k in ("accent", "age", "description", "use_case")
                    if labels.get(k)
                )
                voices.append({
                    "id": entry.get("voice_id"),
                    "label": entry.get("name") or entry.get("voice_id"),
                    "tone": tone,
                    "hint": labels.get("gender", ""),
                    # Their own sample costs nothing to play, unlike synthesising one.
                    "preview_url": entry.get("preview_url") or "",
                })
            kept = [v for v in voices if v["id"]]
            out = {"provider": provider, "voices": kept,
                   "default": config.default_voice("elevenlabs"), "source": source}
            if not kept:
                out["error"] = (
                    "Kalit ishladi, lekin bitta ham ovoz qaytmadi — "
                    "API kalitiga 'voices_read' ruxsatini bering."
                )
            return out
        except Exception as exc:  # noqa: BLE001
            return {"provider": provider, "voices": [], "default": "",
                    "error": _readable(exc)}

    return {"provider": provider, "voices": [], "default": ""}


def _readable(exc: Exception) -> str:
    """Say what actually went wrong, in words that suggest a next step."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 401:
            return "ElevenLabs kalitni qabul qilmadi (401) — kalitni tekshiring."
        if code == 403:
            return ("Kalitda yetarli ruxsat yo'q (403) — "
                    "'voices_read' va 'text_to_speech' ni yoqing.")
        if code == 429:
            return "ElevenLabs hozir band (429) — bir ozdan keyin urinib ko'ring."
        return f"ElevenLabs {code}: {exc.response.text[:160]}"
    return str(exc)[:200] or exc.__class__.__name__


async def _elevenlabs_voices() -> tuple[list[dict], str]:
    """Every voice the key can reach, newest API first.

    v2 is the listing endpoint now: it pages, and it is the only one that returns
    voices shared into the account from the Voice Library. v1 is kept as a
    fallback for keys or deployments where v2 is not available, because losing
    the whole picker is a far worse outcome than an older shape.
    """
    headers = {"xi-api-key": config.key("elevenlabs")}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        try:
            found: list[dict] = []
            page: str | None = None
            for _ in range(10):        # 1000 voices is far past anyone's library
                params = {"page_size": 100}
                if page:
                    params["next_page_token"] = page
                resp = await client.get("https://api.elevenlabs.io/v2/voices",
                                        headers=headers, params=params)
                resp.raise_for_status()
                body = resp.json()
                found += body.get("voices") or []
                page = body.get("next_page_token")
                if not page or not body.get("has_more"):
                    break
            if found:
                return found, "v2"
        except httpx.HTTPStatusError as exc:
            # A key problem will repeat on v1, so report it rather than
            # retrying and returning the same failure under another name.
            if exc.response.status_code in (401, 403):
                raise
        except Exception:  # noqa: BLE001 - fall through to v1
            pass

        resp = await client.get("https://api.elevenlabs.io/v1/voices",
                                headers=headers, params={"show_legacy": "true"})
        resp.raise_for_status()
        return resp.json().get("voices") or [], "v1"


def _preview_path(provider: str, voice_id: str, language: str) -> Path:
    key = hashlib.sha1(f"{provider}:{voice_id}:{language}".encode()).hexdigest()[:16]
    return config.DATA_DIR / "previews" / f"{provider}_{key}"


async def preview(provider: str, voice_id: str, language: str = "en") -> Path:
    """A cached spoken sample of one voice.

    ElevenLabs hands out its own sample for free, so that is used where it
    exists — hearing a voice should not quietly spend the user's credits.
    """
    provider = (provider or config.TTS_PROVIDER).lower()
    voice_id = (voice_id or config.default_voice(provider)).strip()
    if not voice_id:
        raise ValueError("Ovoz tanlanmagan.")
    language = language if language in SAMPLES else "en"

    base = _preview_path(provider, voice_id, language)
    for existing in base.parent.glob(f"{base.name}.*"):
        if existing.stat().st_size > 0:
            return existing
    base.parent.mkdir(parents=True, exist_ok=True)

    if provider == "elevenlabs":
        catalogue = await list_voices("elevenlabs")
        url = next((v.get("preview_url") for v in catalogue.get("voices", [])
                    if v["id"] == voice_id and v.get("preview_url")), "")
        if url:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                resp = await client.get(url)
                resp.raise_for_status()
            target = base.with_suffix(".mp3")
            target.write_bytes(resp.content)
            return target

    path, _words = await tts.synthesize(
        text=SAMPLES[language], out_path=base, provider=provider,
        voice_id=voice_id, attempts=2,
    )
    return path
