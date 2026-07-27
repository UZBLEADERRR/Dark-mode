"""Voice-over generation.

Each adapter writes an audio file and, where the provider supports it, returns
word-level timings. When it doesn't, the caller falls back to `align.py`.
"""

from __future__ import annotations

import asyncio
import base64
import re
import struct
from pathlib import Path

import httpx

from .. import config


class TTSError(RuntimeError):
    pass


LANG_HINTS = {
    "en": "en",
    "uz": "uz",
    "ru": "ru",
    "tr": "tr",
    "es": "es",
    "ar": "ar",
    "hi": "hi",
    "de": "de",
    "fr": "fr",
}


def _wav_from_pcm(pcm: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Gemini returns headerless 16-bit PCM; ffmpeg wants a container."""
    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits)
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def _rate_from_mime(mime: str) -> int:
    match = re.search(r"rate=(\d+)", mime or "")
    return int(match.group(1)) if match else 24000


def _words_from_chars(
    characters: list[str], starts: list[float], ends: list[float]
) -> list[dict]:
    """Collapse ElevenLabs character timings into word timings."""
    words: list[dict] = []
    buffer: list[str] = []
    start: float | None = None
    end: float = 0.0

    for char, char_start, char_end in zip(characters, starts, ends):
        if char.isspace():
            if buffer:
                words.append({"text": "".join(buffer), "start": start or 0.0, "end": end})
                buffer, start = [], None
            continue
        if start is None:
            start = float(char_start)
        buffer.append(char)
        end = float(char_end)

    if buffer:
        words.append({"text": "".join(buffer), "start": start or 0.0, "end": end})
    return words


# --- ElevenLabs --------------------------------------------------------------

async def _elevenlabs(
    client: httpx.AsyncClient, text: str, out_path: Path, voice_id: str | None
) -> tuple[Path, list[dict]]:
    if not config.ELEVENLABS_API_KEY:
        raise TTSError("ELEVENLABS_API_KEY is not set.")

    voice = voice_id or config.ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    resp = await client.post(
        url,
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": config.ELEVENLABS_MODEL,
            "output_format": "mp3_44100_128",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15},
        },
    )
    if resp.status_code >= 400:
        raise TTSError(f"ElevenLabs error {resp.status_code}: {resp.text[:300]}")

    payload = resp.json()
    audio_b64 = payload.get("audio_base64")
    if not audio_b64:
        raise TTSError("ElevenLabs returned no audio.")

    out_path = out_path.with_suffix(".mp3")
    out_path.write_bytes(base64.b64decode(audio_b64))

    alignment = payload.get("normalized_alignment") or payload.get("alignment") or {}
    words = _words_from_chars(
        alignment.get("characters", []),
        alignment.get("character_start_times_seconds", []),
        alignment.get("character_end_times_seconds", []),
    )
    return out_path, words


# --- OpenAI ------------------------------------------------------------------

async def _openai(
    client: httpx.AsyncClient, text: str, out_path: Path, voice_id: str | None
) -> tuple[Path, list[dict]]:
    if not config.OPENAI_API_KEY:
        raise TTSError("OPENAI_API_KEY is not set.")

    resp = await client.post(
        f"{config.OPENAI_BASE}/audio/speech",
        headers={
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.OPENAI_TTS_MODEL,
            "voice": voice_id or config.OPENAI_TTS_VOICE,
            "input": text,
            "response_format": "mp3",
        },
    )
    if resp.status_code >= 400:
        raise TTSError(f"OpenAI TTS error {resp.status_code}: {resp.text[:300]}")

    out_path = out_path.with_suffix(".mp3")
    out_path.write_bytes(resp.content)
    return out_path, []


# --- Gemini ------------------------------------------------------------------

async def _gemini(
    client: httpx.AsyncClient, text: str, out_path: Path, voice_id: str | None
) -> tuple[Path, list[dict]]:
    if not config.GEMINI_API_KEY:
        raise TTSError("GEMINI_API_KEY is not set.")

    url = f"{config.GEMINI_BASE}/models/{config.GEMINI_TTS_MODEL}:generateContent"
    resp = await client.post(
        url,
        headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice_id or config.GEMINI_TTS_VOICE
                        }
                    }
                },
            },
        },
    )
    if resp.status_code >= 400:
        raise TTSError(f"Gemini TTS error {resp.status_code}: {resp.text[:300]}")

    for candidate in resp.json().get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                pcm = base64.b64decode(blob["data"])
                rate = _rate_from_mime(blob.get("mimeType") or blob.get("mime_type") or "")
                out_path = out_path.with_suffix(".wav")
                out_path.write_bytes(_wav_from_pcm(pcm, rate))
                return out_path, []
    raise TTSError("Gemini TTS returned no audio.")


# --- public API --------------------------------------------------------------

async def synthesize(
    *,
    text: str,
    out_path: Path,
    provider: str | None = None,
    voice_id: str | None = None,
    attempts: int = 3,
) -> tuple[Path, list[dict]]:
    provider = (provider or config.TTS_PROVIDER).lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(180.0, connect=30.0)
    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(attempts):
            try:
                if provider == "elevenlabs":
                    return await _elevenlabs(client, text, out_path, voice_id)
                if provider == "openai":
                    return await _openai(client, text, out_path, voice_id)
                if provider == "gemini":
                    return await _gemini(client, text, out_path, voice_id)
                raise TTSError(f"Unknown TTS provider '{provider}'.")
            except Exception as exc:  # noqa: BLE001 - retried below
                last_error = exc
                if attempt < attempts - 1:
                    await asyncio.sleep(2 * (attempt + 1))

    raise TTSError(f"Voice generation failed after {attempts} attempts: {last_error}")


def available_providers() -> list[str]:
    return [p for p in ("elevenlabs", "openai", "gemini") if config.tts_provider_ready(p)]
