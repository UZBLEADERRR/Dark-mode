"""Word-level timing for subtitles.

Order of preference: timings that came free with the voice call → a Whisper
transcription of the rendered audio → a proportional estimate from character
counts. The estimate is never as tight, but it keeps the app usable with only an
Anthropic key configured.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from .. import config


def estimate_words(text: str, duration: float) -> list[dict]:
    """Spread words across the clip in proportion to their length.

    Weighting by character count (plus a constant per word) tracks speech far
    better than an even split, because long words genuinely take longer to say.
    """
    tokens = [t for t in re.split(r"\s+", text.strip()) if t]
    if not tokens or duration <= 0:
        return []

    weights = [len(t) + 2 for t in tokens]
    total = sum(weights)

    words: list[dict] = []
    cursor = 0.0
    for token, weight in zip(tokens, weights):
        span = duration * (weight / total)
        words.append({"text": token, "start": round(cursor, 3), "end": round(cursor + span, 3)})
        cursor += span
    return words


async def transcribe_segments(audio_path: Path, language: str | None = None) -> list[dict]:
    """Sentence-level `[{start, end, text}]` for a recording we did not make.

    Dubbing needs to know when each line was spoken, not just what was said, so
    the replacement can land in the same place. Whisper returns segments
    directly; Gemini can listen to audio too, which matters because most
    installations here have only a Gemini key.
    """
    if config.has_key("openai"):
        segments = await _whisper_segments(audio_path, language)
        if segments:
            return segments
    if config.has_key("gemini"):
        return await _gemini_segments(audio_path, language)
    return []


async def _whisper_segments(audio_path: Path, language: str | None) -> list[dict]:
    data = {"model": config.model("openai_transcribe"), "response_format": "verbose_json"}
    if language:
        data["language"] = language
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            resp = await client.post(
                f"{config.OPENAI_BASE}/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.key('openai')}"},
                data=data,
                files={"file": (audio_path.name, audio_path.read_bytes(),
                                "application/octet-stream")},
            )
        resp.raise_for_status()
        return [
            {"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()}
            for s in resp.json().get("segments", []) if s.get("text", "").strip()
        ]
    except Exception:  # noqa: BLE001 - fall through to the next transcriber
        return []


_SEGMENT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "segments": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "start": {"type": "NUMBER"},
                    "end": {"type": "NUMBER"},
                    "text": {"type": "STRING"},
                },
                "required": ["start", "end", "text"],
                "propertyOrdering": ["start", "end", "text"],
            },
        }
    },
    "required": ["segments"],
}


async def _gemini_segments(audio_path: Path, language: str | None) -> list[dict]:
    import base64
    import mimetypes

    mime = mimetypes.guess_type(audio_path.name)[0] or "audio/mpeg"
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inlineData": {"mimeType": mime,
                                "data": base64.b64encode(audio_path.read_bytes()).decode()}},
                {"text": "Transcribe this recording. Break it into the sentences as they "
                         "are actually spoken, and give the start and end of each in "
                         "seconds from the beginning of the file. Do not translate — "
                         "write it in the language being spoken."
                         + (f" The audio is in {language}." if language else "")},
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _SEGMENT_SCHEMA,
        },
    }
    url = f"{config.GEMINI_BASE}/models/{config.model('gemini_text')}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
            resp = await client.post(
                url, headers={"x-goog-api-key": config.key("gemini")}, json=payload)
        resp.raise_for_status()
        text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        import json
        raw = json.loads(text).get("segments", [])
    except Exception:  # noqa: BLE001
        return []

    out = []
    for entry in raw:
        try:
            start, end = float(entry["start"]), float(entry["end"])
        except (KeyError, TypeError, ValueError):
            continue
        body = str(entry.get("text", "")).strip()
        if body and end > start:
            out.append({"start": start, "end": end, "text": body})
    return sorted(out, key=lambda s: s["start"])


async def transcribe_words(audio_path: Path, language: str | None = None) -> list[dict]:
    """Word timings from OpenAI transcription. Returns [] if unavailable."""
    if not config.has_key("openai"):
        return []

    data = {
        "model": config.model("openai_transcribe"),
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language:
        data["language"] = language

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0)) as client:
            resp = await client.post(
                f"{config.OPENAI_BASE}/audio/transcriptions",
                headers={"Authorization": f"Bearer {config.key('openai')}"},
                data=data,
                files={"file": (audio_path.name, audio_path.read_bytes(), "application/octet-stream")},
            )
        if resp.status_code >= 400:
            return []
        payload = resp.json()
    except Exception:  # noqa: BLE001 - alignment is best-effort
        return []

    words = []
    for word in payload.get("words") or []:
        text = (word.get("word") or "").strip()
        if not text:
            continue
        words.append(
            {"text": text, "start": float(word.get("start", 0.0)), "end": float(word.get("end", 0.0))}
        )
    return words


async def transcribe_full(audio_path: Path, language: str | None = None) -> dict:
    """Full transcript plus word timings — used when the user uploads their own audio."""
    if not config.has_key("openai"):
        return {"text": "", "words": []}

    data = {
        "model": config.model("openai_transcribe"),
        "response_format": "verbose_json",
        "timestamp_granularities[]": "word",
    }
    if language:
        data["language"] = language

    async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=30.0)) as client:
        resp = await client.post(
            f"{config.OPENAI_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {config.key('openai')}"},
            data=data,
            files={"file": (audio_path.name, audio_path.read_bytes(), "application/octet-stream")},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Transcription failed {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    words = [
        {
            "text": (w.get("word") or "").strip(),
            "start": float(w.get("start", 0.0)),
            "end": float(w.get("end", 0.0)),
        }
        for w in (payload.get("words") or [])
        if (w.get("word") or "").strip()
    ]
    return {"text": payload.get("text", "").strip(), "words": words}


async def words_for(
    *,
    audio_path: Path,
    text: str,
    duration: float,
    provider: str,
    language: str | None,
    provider_words: list[dict] | None = None,
) -> list[dict]:
    if provider_words:
        return provider_words
    if provider == "whisper":
        words = await transcribe_words(audio_path, language)
        if words:
            return words
    return estimate_words(text, duration)
