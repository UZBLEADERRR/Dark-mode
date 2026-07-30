"""The cheap, slow road: many requests in one batch instead of one at a time.

Gemini's Batch API is half the price and answers within a day rather than within
a second. That trade is worthless for a video somebody is waiting for, and very
good indeed for one that is not due until Tuesday — which is the only place this
is used: a planned video whose slot is hours away.

Everything about it is best-effort by design. A batch that is refused, or that
has not answered by the time the video is needed, falls back to generating the
ordinary way; the caller gets pictures either way, and the only difference is
what they cost. That is why nothing here raises for a slow batch — the fallback
is the feature, not the failure.

The wire format, for the reader who has to change it later:

    POST {base}/models/{model}:batchGenerateContent
      {"batch": {"displayName": …,
                 "inputConfig": {"requests": [{"request": {…}, "metadata": {"key": …}}]}}}
    → {"name": "batches/abc123"}

    GET {base}/batches/abc123
    → {"metadata": {"state": "JOB_STATE_SUCCEEDED",
                    "output": {"inlinedResponses": {"inlinedResponses": [
                       {"metadata": {"key": …}, "response": {…}} | {"error": {…}}]}}}}
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import httpx

from .. import config, keys

DONE = "JOB_STATE_SUCCEEDED"
PARTIAL = "JOB_STATE_PARTIALLY_SUCCEEDED"
BAD = {"JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"}
FINISHED = {DONE, PARTIAL} | BAD

# How long to wait for a batch before giving up and paying full price. Well under
# Gemini's own 24-hour promise, because a video with six hours until its slot
# cannot spend all of them waiting.
DEFAULT_PATIENCE_SECONDS = 3 * 3600
POLL_SECONDS = 30


class BatchError(RuntimeError):
    pass


def available() -> bool:
    """Batching exists only on Gemini here, and only with a key for it."""
    return config.has_key("gemini")


# Which key submitted which batch. A batch belongs to the project that created
# it, so key rotation has to stop at the submit call: polling with a different
# key is a 403 on somebody else's job, not a fresh allowance.
_owner: dict[str, str] = {}


def _headers(secret: str) -> dict[str, str]:
    return {"x-goog-api-key": secret, "Content-Type": "application/json"}


async def submit(model: str, requests: list[dict[str, Any]], *, label: str = "sarideo") -> str:
    """Hand over a list of `{key, request}` and get the batch's name back.

    `key` is how a result is matched to what asked for it — the order of inlined
    responses is not promised, and matching by position is the bug that would
    silently put scene nine's picture on scene two.
    """
    if not available():
        raise BatchError("Gemini kaliti yo'q — batch ishlamaydi.")
    if not requests:
        raise BatchError("Batch bo'sh.")

    body = {
        "batch": {
            "displayName": label[:120],
            "inputConfig": {
                "requests": [
                    {"request": item["request"], "metadata": {"key": str(item["key"])}}
                    for item in requests
                ]
            },
        }
    }
    last = ""
    # A refused submit is worth re-offering to another key: one key's day being
    # spent says nothing about the next key's.
    for _ in range(max(1, min(keys.count("gemini"), 6))):
        secret = config.key("gemini")
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            resp = await client.post(
                f"{config.GEMINI_BASE}/models/{model}:batchGenerateContent",
                headers=_headers(secret), json=body)
        if resp.status_code < 400:
            keys.bless("gemini", secret)
            name = (resp.json() or {}).get("name") or ""
            if not name:
                raise BatchError("Batch nomi qaytmadi.")
            _owner[name] = secret
            return name
        keys.penalise("gemini", secret, status=resp.status_code, body=resp.text[:400])
        last = f"{resp.status_code}: {resp.text[:300]}"
        if not keys.can_switch("gemini", secret):
            break
    raise BatchError(f"Batch qabul qilinmadi {last}")


async def poll(name: str) -> dict[str, Any]:
    """One look. Returns `{state, results: {key: response}, errors: {key: text}}`."""
    secret = _owner.get(name) or config.key("gemini")
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
        resp = await client.get(f"{config.GEMINI_BASE}/{name}", headers=_headers(secret))
    if resp.status_code >= 400:
        raise BatchError(f"Batch holati o'qilmadi {resp.status_code}: {resp.text[:200]}")

    payload = resp.json() or {}
    meta = payload.get("metadata") or {}
    state = meta.get("state") or ""
    inlined = (((meta.get("output") or {}).get("inlinedResponses") or {})
               .get("inlinedResponses") or [])

    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for item in inlined:
        key = str(((item.get("metadata") or {}).get("key")) or "")
        if not key:
            continue
        if item.get("error"):
            errors[key] = str((item["error"] or {}).get("message") or item["error"])[:300]
        elif item.get("response") is not None:
            results[key] = item["response"]
    return {"state": state, "results": results, "errors": errors,
            "finished": state in FINISHED}


async def gather(
    name: str,
    *,
    patience: float | None = None,
    on_wait=None,
) -> dict[str, Any]:
    """Wait for a batch, within reason. Never raises for being slow.

    A batch that is still running when patience runs out comes back
    `finished: False` with whatever it had — the caller then makes the rest the
    ordinary way rather than being stuck. Partial success is treated the same as
    success, because a batch that answered forty of fifty requests has saved the
    price of forty.
    """
    # Read here, not in the signature: a default bound at import time cannot be
    # tuned, and how long it is worth waiting depends on when the video is due.
    if patience is None:
        patience = DEFAULT_PATIENCE_SECONDS
    waited = 0.0
    looks = 0
    while True:
        try:
            look = await poll(name)
        except BatchError as exc:
            return {"state": "unreachable", "results": {}, "errors": {},
                    "finished": False, "why": str(exc)}
        if look["finished"]:
            return look
        if waited >= patience:
            return {**look, "why": "batch belgilangan vaqtda tugamadi"}
        # Said occasionally rather than every poll: a three-hour wait at one line
        # every thirty seconds is three hundred and sixty lines of nothing.
        looks += 1
        if on_wait and looks % 10 == 1:
            on_wait(waited, look["state"])
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS


# --- turning a batch response back into an image ------------------------------

def image_bytes(response: dict[str, Any]) -> bytes | None:
    """The picture out of one `GenerateContentResponse`, or None.

    Same shape as the streaming path uses, so a batch result and a live one are
    the same thing by the time anything else sees them.
    """
    for candidate in (response.get("candidates") or []):
        for part in ((candidate.get("content") or {}).get("parts") or []):
            blob = part.get("inlineData") or part.get("inline_data")
            data = (blob or {}).get("data")
            if data:
                try:
                    return base64.b64decode(data)
                except (ValueError, TypeError):
                    continue
    return None


def image_request(model: str, prompt: str, *, negative: str = "",
                  images: list[tuple[bytes, str]] | None = None) -> dict[str, Any]:
    """One picture, in the shape `submit` wants.

    The model goes in the request as well as the URL: the batch endpoint takes it
    in the path, and each inlined request repeats it — which is what lets a
    reader of this JSON tell what it was going to run.
    """
    text = prompt if not negative else f"{prompt}\n\nAvoid: {negative}"
    parts: list[dict[str, Any]] = [
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(raw).decode()}}
        for raw, mime in (images or [])
    ]
    parts.append({"text": text})
    return {
        "model": f"models/{model}",
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["IMAGE"]},
    }
