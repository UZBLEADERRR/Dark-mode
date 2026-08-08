"""A voice that costs nothing: Microsoft Edge's read-aloud service.

Every other narrator in this app is sold by the word. This one is the engine
behind Edge's "read aloud" button, it wants no account and no key, and it has a
man and a woman in most languages — including Uzbek, which is rarer than it
should be.

It is not an API anybody publishes a contract for, so it is offered as one
choice among several rather than made the default: when it stops working the
answer is to pick another provider, not to have the app stop working.

The obvious alternative — the browser's own `speechSynthesis` — cannot be used
here at all. The video is assembled by ffmpeg on the server and needs an audio
*file*; a browser can speak that text aloud but has no supported way to hand the
sound back as data. So the free voice has to come from a service, and this is
the one that asks for nothing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# What the picker offers, per language: one woman, one man. Written down rather
# than fetched, because a list that needs a network call to draw a dropdown is a
# dropdown that is sometimes empty — and these names have been stable for years.
VOICES: dict[str, list[dict[str, str]]] = {
    "uz": [
        {"id": "uz-UZ-MadinaNeural", "label": "Madina", "hint": "ayol", "tone": "o'zbek"},
        {"id": "uz-UZ-SardorNeural", "label": "Sardor", "hint": "erkak", "tone": "o'zbek"},
    ],
    "en": [
        {"id": "en-US-AriaNeural", "label": "Aria", "hint": "ayol", "tone": "US"},
        {"id": "en-US-GuyNeural", "label": "Guy", "hint": "erkak", "tone": "US"},
        {"id": "en-GB-SoniaNeural", "label": "Sonia", "hint": "ayol", "tone": "UK"},
        {"id": "en-GB-RyanNeural", "label": "Ryan", "hint": "erkak", "tone": "UK"},
    ],
    "ru": [
        {"id": "ru-RU-SvetlanaNeural", "label": "Svetlana", "hint": "ayol", "tone": "rus"},
        {"id": "ru-RU-DmitryNeural", "label": "Dmitry", "hint": "erkak", "tone": "rus"},
    ],
    "tr": [
        {"id": "tr-TR-EmelNeural", "label": "Emel", "hint": "ayol", "tone": "turk"},
        {"id": "tr-TR-AhmetNeural", "label": "Ahmet", "hint": "erkak", "tone": "turk"},
    ],
    "ar": [
        {"id": "ar-SA-ZariyahNeural", "label": "Zariyah", "hint": "ayol", "tone": "arab"},
        {"id": "ar-SA-HamedNeural", "label": "Hamed", "hint": "erkak", "tone": "arab"},
    ],
    "es": [
        {"id": "es-ES-ElviraNeural", "label": "Elvira", "hint": "ayol", "tone": "ispan"},
        {"id": "es-ES-AlvaroNeural", "label": "Alvaro", "hint": "erkak", "tone": "ispan"},
    ],
    "de": [
        {"id": "de-DE-KatjaNeural", "label": "Katja", "hint": "ayol", "tone": "nemis"},
        {"id": "de-DE-ConradNeural", "label": "Conrad", "hint": "erkak", "tone": "nemis"},
    ],
    "fr": [
        {"id": "fr-FR-DeniseNeural", "label": "Denise", "hint": "ayol", "tone": "fransuz"},
        {"id": "fr-FR-HenriNeural", "label": "Henri", "hint": "erkak", "tone": "fransuz"},
    ],
    "hi": [
        {"id": "hi-IN-SwaraNeural", "label": "Swara", "hint": "ayol", "tone": "hind"},
        {"id": "hi-IN-MadhurNeural", "label": "Madhur", "hint": "erkak", "tone": "hind"},
    ],
    "ko": [
        {"id": "ko-KR-SunHiNeural", "label": "Sun-Hi", "hint": "ayol", "tone": "koreys"},
        {"id": "ko-KR-InJoonNeural", "label": "InJoon", "hint": "erkak", "tone": "koreys"},
    ],
}

DEFAULT = "uz-UZ-MadinaNeural"


def catalogue(language: str = "") -> list[dict[str, str]]:
    """The voices worth showing, the asked-for language first.

    All of them, not only the matching ones: an Uzbek video narrated in English
    is a thing people do on purpose, and a picker that hides the other languages
    makes it look impossible.
    """
    want = (language or "").split("-")[0].lower()
    order = ([want] if want in VOICES else []) + [k for k in VOICES if k != want]
    return [{**voice, "tone": f"{voice['tone']} · bepul"}
            for key in order for voice in VOICES[key]]


class EdgeError(RuntimeError):
    """Edge would not read the line."""


async def speak(text: str, out_path: Path, voice_id: str | None = None) -> Path:
    """Write this line as an mp3. Raises `EdgeError` with a readable reason."""
    try:
        import edge_tts
    except ImportError as exc:  # pragma: no cover - a deploy that skipped it
        raise EdgeError(
            "edge-tts o'rnatilmagan — serverni qayta deploy qiling.") from exc

    voice = (voice_id or DEFAULT).strip() or DEFAULT
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        speech = edge_tts.Communicate(text, voice)
        # Written through a temporary name and moved into place, so a failure
        # halfway leaves no half-file for the render to pick up as finished.
        part = out_path.with_suffix(out_path.suffix + ".part")
        await speech.save(str(part))
        if not part.exists() or part.stat().st_size < 512:
            raise EdgeError("Edge bo'sh audio qaytardi.")
        part.replace(out_path)
    except EdgeError:
        raise
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - one class out, whatever came in
        raise EdgeError(_readable(exc, voice)) from exc
    return out_path


def _readable(exc: Exception, voice: str) -> str:
    said = " ".join(str(exc).split()) or exc.__class__.__name__
    low = said.lower()
    if "no audio" in low or "no server date" in low or "403" in low:
        # Both of these are the same thing wearing different words: the service
        # decided this caller is not Edge. Nothing about the text or the voice
        # will change that, so say what will.
        return ("Bepul ovoz xizmati so'rovni rad etdi. Bu vaqtinchalik bo'lishi "
                "mumkin — birozdan keyin urinib ko'ring yoki Kutubxona → "
                "Modellar dan boshqa ovoz provayderini tanlang.")
    if "not a valid" in low or "voice" in low and "invalid" in low:
        return f"'{voice}' — bunday ovoz yo'q."
    return f"Bepul ovoz xizmati javob bermadi: {said[:200]}"
