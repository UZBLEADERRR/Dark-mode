"""Voice-over generation.

Each adapter writes an audio file and, where the provider supports it, returns
word-level timings. When it doesn't, the caller falls back to `align.py`.
"""

from __future__ import annotations

import asyncio
import base64
import re
import struct
import time
from pathlib import Path
from typing import Callable

import httpx

from .. import config
from . import ratelimit


class TTSError(RuntimeError):
    pass


class RateLimited(TTSError):
    """The provider refused because we are over its per-minute allowance.

    Kept apart from every other failure because it is not one: the request was
    understood and will succeed on its own once the window rolls over. Waiting
    is the correct response, so this never counts against the stall deadline.
    """

    def __init__(self, message: str, retry_after: float = 20.0) -> None:
        super().__init__(message)
        self.retry_after = retry_after


# One ceiling per provider, shared across the whole process. Shared because
# scenes are voiced concurrently and three workers would otherwise get the full
# allowance each; per provider because an allowance belongs to a key, and being
# throttled by Gemini is no reason to hold back an ElevenLabs call.
_limiters: dict[str, ratelimit.RateLimiter] = {}


def limiter(provider: str | None = None) -> ratelimit.RateLimiter:
    """This provider's limiter, resynced to config so a settings change lands."""
    provider = (provider or config.TTS_PROVIDER).lower()
    gate = _limiters.get(provider)
    if gate is None:
        gate = _limiters[provider] = ratelimit.RateLimiter(config.tts_rate_limit(provider))
    else:
        gate.reconfigure(config.tts_rate_limit(provider))
    return gate


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


def _raise_for_status(resp: httpx.Response, label: str) -> None:
    """Turn a failed response into the right kind of error.

    A 429 — and a 503, which providers use for the same "come back shortly" —
    becomes `RateLimited` so the caller waits instead of burning an attempt.
    """
    if resp.status_code < 400:
        return
    if resp.status_code in (429, 503):
        raise RateLimited(
            f"{label} is rate limiting us ({resp.status_code}).",
            ratelimit.retry_after(resp.headers, 20.0),
        )
    raise TTSError(f"{label} error {resp.status_code}: {resp.text[:300]}")


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

    voice = voice_id or config.default_voice("elevenlabs")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
    resp = await client.post(
        url,
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": config.model("elevenlabs_tts"),
            "output_format": "mp3_44100_128",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15},
        },
    )
    _raise_for_status(resp, "ElevenLabs")

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
            "model": config.model("openai_tts"),
            "voice": voice_id or config.default_voice("openai"),
            "input": text,
            "response_format": "mp3",
        },
    )
    _raise_for_status(resp, "OpenAI TTS")

    out_path = out_path.with_suffix(".mp3")
    out_path.write_bytes(resp.content)
    return out_path, []


# --- Gemini ------------------------------------------------------------------

async def _gemini(
    client: httpx.AsyncClient, text: str, out_path: Path, voice_id: str | None
) -> tuple[Path, list[dict]]:
    if not config.GEMINI_API_KEY:
        raise TTSError("GEMINI_API_KEY is not set.")

    body = {
        "contents": [{"role": "user", "parts": [{"text": text}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_id or config.default_voice("gemini")
                    }
                }
            },
        },
    }
    headers = {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}

    async def call(model_name: str) -> httpx.Response:
        return await client.post(
            f"{config.GEMINI_BASE}/models/{model_name}:generateContent",
            headers=headers, json=body,
        )

    resp = await call(config.model("gemini_tts"))
    fallback = config.model("gemini_tts_fallback")
    # The default voice model is a preview build, which a given key may simply
    # not be granted. Dropping to the settled one beats failing the whole video.
    # A 429 is deliberately not in this list: the model is fine, the window is
    # full, and switching models would only spend the other model's allowance.
    if resp.status_code in (400, 403, 404) and fallback \
            and fallback != config.model("gemini_tts"):
        resp = await call(fallback)
    _raise_for_status(resp, "Gemini TTS")

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
    on_retry: Callable[[int, Exception], None] | None = None,
    on_wait: Callable[[float, str], None] | None = None,
) -> tuple[Path, list[dict]]:
    """Speak one line: paced to the provider's allowance, bounded against a stall.

    Two kinds of waiting happen here and they are deliberately kept apart.

    Being throttled is not a failure. The request was understood and will
    succeed once the window rolls over, so the limiter holds the call and a 429
    is slept off — outside the deadline, because a clock that punishes patience
    would turn a working key into a failed video.

    A provider that accepts the request and never answers is the failure worth
    catching, and `TTS_DEADLINE` bounds it. Without that ceiling, three retries
    behind a generous per-call timeout hold this line, and every line queued
    behind it, for many minutes with nothing to show for the wait.
    """
    provider = (provider or config.TTS_PROVIDER).lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(config.TTS_TIMEOUT, connect=20.0)
    gate = limiter(provider)
    last_error: Exception | None = None

    async def run() -> tuple[Path, list[dict]]:
        nonlocal last_error
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
                except (asyncio.CancelledError, RateLimited):
                    raise
                except Exception as exc:  # noqa: BLE001 - retried below
                    last_error = exc
                    if attempt < attempts - 1:
                        if on_retry:
                            on_retry(attempt + 1, exc)
                        await asyncio.sleep(2 * (attempt + 1))
        raise TTSError(f"Voice generation failed after {attempts} attempts: {last_error}")

    queued = 0.0
    while True:
        started = time.monotonic()
        await gate.acquire(
            on_wait=(lambda d: on_wait(d, "queued behind the rate limit")) if on_wait else None)
        queued += time.monotonic() - started

        try:
            return await asyncio.wait_for(run(), timeout=config.TTS_DEADLINE)
        except RateLimited as exc:
            # Every worker backs off, not just this one — the allowance is shared,
            # so letting the others carry on would just collect more refusals.
            gate.penalise(exc.retry_after)
            queued += exc.retry_after
            if queued > config.TTS_RATE_PATIENCE:
                raise TTSError(
                    f"Still rate limited after {queued / 60:.0f} minutes of waiting. "
                    "Lower TTS_RATE_LIMIT or use a key with a bigger allowance."
                ) from exc
            if on_wait:
                on_wait(exc.retry_after, "rate limited by the provider")
            await asyncio.sleep(exc.retry_after)
        except asyncio.TimeoutError as exc:
            raise TTSError(
                f"The voice provider did not answer within {config.TTS_DEADLINE:.0f}s"
                + (f" (last error: {last_error})" if last_error else "")
            ) from exc


def available_providers() -> list[str]:
    return [p for p in ("elevenlabs", "openai", "gemini") if config.tts_provider_ready(p)]


# --- reading a whole passage at once -----------------------------------------

# What separates one scene's line from the next inside a batched request. A
# blank line is read as a paragraph break, which is the pause a scene change
# wants anyway, and it gives the splitter an unambiguous place to cut.
BATCH_SEPARATOR = "\n\n"


def batches(lines: list[str], *, max_chars: int, max_lines: int) -> list[list[int]]:
    """Group line indexes into requests that stay under the provider's limits.

    Returned as indexes rather than text so the caller can map results back to
    whatever the lines belonged to. A single line longer than the cap gets a
    request of its own — clipping it would lose words.
    """
    groups: list[list[int]] = []
    current: list[int] = []
    size = 0
    for i, line in enumerate(lines):
        cost = len(line) + len(BATCH_SEPARATOR)
        if current and (size + cost > max_chars or len(current) >= max_lines):
            groups.append(current)
            current, size = [], 0
        current.append(i)
        size += cost
    if current:
        groups.append(current)
    return groups


def _cut_points(lines: list[str], characters: list[str],
                ends: list[float]) -> list[float] | None:
    """Where each line finishes, in seconds. None when the timings do not line up.

    The provider echoes back one entry per character it actually spoke, so the
    end of line *i* is the timing of the last character of that line in the
    joined text. If the echo is not the text we sent — a provider that
    normalises numbers, say — the arithmetic is meaningless and the caller falls
    back to reading each line on its own rather than cutting in the wrong place.
    """
    joined = BATCH_SEPARATOR.join(lines)
    if len(characters) != len(joined) or len(ends) != len(characters):
        return None
    if "".join(characters) != joined:
        return None

    cuts: list[float] = []
    cursor = 0
    for line in lines:
        cursor += len(line)
        cuts.append(float(ends[max(0, cursor - 1)]))
        cursor += len(BATCH_SEPARATOR)
    return cuts


def _words_in_span(characters: list[str], starts: list[float], ends: list[float],
                   first: int, last: int, offset: float) -> list[dict]:
    """Word timings for one line, rebased so the line starts at zero."""
    words = _words_from_chars(characters[first:last], starts[first:last], ends[first:last])
    return [{**w, "start": max(0.0, w["start"] - offset), "end": max(0.0, w["end"] - offset)}
            for w in words]


async def _elevenlabs_batch(
    client: httpx.AsyncClient, lines: list[str], voice_id: str | None
) -> tuple[bytes, list[str], list[float], list[float]] | None:
    """Speak several lines in one request. None when the response cannot be split."""
    voice = voice_id or config.default_voice("elevenlabs")
    resp = await client.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"},
        json={
            "text": BATCH_SEPARATOR.join(lines),
            "model_id": config.model("elevenlabs_tts"),
            "output_format": "mp3_44100_128",
            "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.15},
        },
    )
    _raise_for_status(resp, "ElevenLabs")

    payload = resp.json()
    audio_b64 = payload.get("audio_base64")
    if not audio_b64:
        raise TTSError("ElevenLabs returned no audio.")

    # `alignment` echoes the text we sent; `normalized_alignment` echoes what the
    # model decided to say instead. Only the first can be mapped back to our
    # line boundaries, so a response carrying only the second is not splittable.
    align = payload.get("alignment") or {}
    characters = align.get("characters") or []
    starts = align.get("character_start_times_seconds") or []
    ends = align.get("character_end_times_seconds") or []
    if not characters or len(starts) != len(characters):
        return None
    return base64.b64decode(audio_b64), characters, starts, ends


def can_batch(provider: str | None = None) -> bool:
    """Only a provider that times every character can be cut back apart."""
    provider = (provider or config.TTS_PROVIDER).lower()
    return bool(config.TTS_BATCH and provider == "elevenlabs" and config.ELEVENLABS_API_KEY)


async def synthesize_many(
    *,
    lines: list[str],
    out_paths: list[Path],
    provider: str | None = None,
    voice_id: str | None = None,
    on_retry: Callable[[int, Exception], None] | None = None,
    on_wait: Callable[[float, str], None] | None = None,
    on_done: Callable[[int], None] | None = None,
) -> list[tuple[Path, list[dict]]]:
    """Speak many lines, in as few requests as the provider allows.

    Where a provider times every character — only ElevenLabs today — a group of
    lines is read as one passage and cut back apart at the exact moment each line
    ends. That is worth doing for two reasons: fifty-eight requests become three,
    which matters when the key is metered by the minute; and the narrator keeps
    its intonation across sentences instead of starting afresh at every full
    stop, which is the difference between a reading and a list.

    Anything that cannot be cut confidently falls back to one request per line,
    so the result is always the same shape: a file and its word timings, per line.
    """
    from ..render import video          # local: only the batched path needs it

    provider = (provider or config.TTS_PROVIDER).lower()
    results: list[tuple[Path, list[dict]] | None] = [None] * len(lines)

    async def one(i: int) -> None:
        results[i] = await synthesize(
            text=lines[i], out_path=out_paths[i], provider=provider,
            voice_id=voice_id, on_retry=on_retry, on_wait=on_wait,
        )
        if on_done:
            on_done(1)

    if not can_batch(provider) or len(lines) < 2:
        await asyncio.gather(*(one(i) for i in range(len(lines))))
        return [r for r in results if r is not None]

    gate = limiter(provider)
    timeout = httpx.Timeout(config.TTS_TIMEOUT * 3, connect=20.0)

    for group in batches(lines, max_chars=config.TTS_BATCH_CHARS,
                         max_lines=config.TTS_BATCH_LINES):
        if len(group) < 2:
            await one(group[0])
            continue

        chunk = [lines[i] for i in group]
        spoken = None
        try:
            await gate.acquire(
                on_wait=(lambda d: on_wait(d, "queued behind the rate limit"))
                if on_wait else None)
            async with httpx.AsyncClient(timeout=timeout) as client:
                spoken = await _elevenlabs_batch(client, chunk, voice_id)
        except RateLimited as exc:
            gate.penalise(exc.retry_after)
            if on_wait:
                on_wait(exc.retry_after, "rate limited by the provider")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one line at a time still works
            if on_retry:
                on_retry(1, exc)

        cuts = None
        if spoken is not None:
            audio, characters, starts, ends = spoken
            cuts = _cut_points(chunk, characters, ends)

        if cuts is None:
            # Either the request failed or the echo did not match what we sent.
            # Cutting on a guess would put the picture on the wrong words, so
            # this group is simply read line by line instead.
            await asyncio.gather(*(one(i) for i in group))
            continue

        whole = out_paths[group[0]].with_name(f"batch_{group[0]:04d}.mp3")
        whole.parent.mkdir(parents=True, exist_ok=True)
        whole.write_bytes(audio)

        start = 0.0
        cursor = 0
        for offset, index in enumerate(group):
            end = cuts[offset]
            target = out_paths[index].with_suffix(".mp3")
            await video.slice_audio(whole, target, start, end)
            last = cursor + len(chunk[offset])
            results[index] = (
                target,
                _words_in_span(characters, starts, ends, cursor, last, start),
            )
            cursor = last + len(BATCH_SEPARATOR)
            start = end
            if on_done:
                on_done(1)
        whole.unlink(missing_ok=True)

    missing = [i for i, r in enumerate(results) if r is None]
    if missing:
        await asyncio.gather(*(one(i) for i in missing))
    return [r for r in results if r is not None]
