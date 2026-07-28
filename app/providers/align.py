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


async def transcribe_words(audio_path: Path, language: str | None = None) -> list[dict]:
    """Word timings from OpenAI transcription. Returns [] if unavailable."""
    if not config.OPENAI_API_KEY:
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
                headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
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
    if not config.OPENAI_API_KEY:
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
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}"},
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
