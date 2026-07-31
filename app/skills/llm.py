"""The model behind every skill, with an adapter per provider.

Claude is used when an Anthropic key is present; otherwise the same prompts run
on Gemini, so a deployment holding only `GEMINI_API_KEY` still gets scripts,
image prompts, subtitle breaks and metadata from that one key.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from typing import Any

import httpx

from .. import config, keys


class LLMError(RuntimeError):
    pass


class Refused(LLMError):
    """The model answered with a refusal that a different key might not give.

    Carries the key and whether that key was benched, so the caller can tell
    "this key is out of allowance" — worth trying the next one immediately —
    from "the service is down", which no key of ours can fix.
    """

    def __init__(self, message: str, key: str = "", benched: float = 0.0) -> None:
        super().__init__(message)
        self.key = key
        self.benched = benched


# ── Claude ────────────────────────────────────────────────────────────────────

# Keyed by secret, because rotating keys must rotate clients: a client built
# around the key that has just run out of allowance would keep using it.
_anthropic_clients: dict[str, Any] = {}
# Set once we learn the SDK/model rejects output_config, so we stop retrying it.
_structured_outputs_ok = True


def _client(secret: str):
    client = _anthropic_clients.get(secret)
    if client is None:
        import anthropic

        client = _anthropic_clients[secret] = anthropic.Anthropic(api_key=secret)
    return client


def _claude_sync(system: str, user: str, schema: dict | None, max_tokens: int,
                 images: list[tuple[bytes, str]] | None = None) -> str:
    global _structured_outputs_ok
    import anthropic

    secret = config.key("anthropic")
    if not secret:
        raise LLMError("Anthropic kaliti yo'q — kutubxonadan qo'shing yoki "
                       "ANTHROPIC_API_KEY ni sozlang.")

    content: Any = user
    if images:
        # Pictures first: a model reads the instruction better when it already
        # has the thing the instruction is about.
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": mime,
                                         "data": base64.b64encode(data).decode()}}
            for data, mime in images
        ] + [{"type": "text", "text": user}]

    base: dict[str, Any] = {
        "model": config.model("anthropic_text"),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": content}],
    }

    use_schema = bool(schema) and _structured_outputs_ok
    kwargs = dict(base)
    kwargs["output_config"] = {"effort": config.LLM_EFFORT}
    if use_schema:
        kwargs["output_config"]["format"] = {"type": "json_schema", "schema": schema}

    try:
        try:
            with _client(secret).messages.stream(**kwargs) as stream:
                message = stream.get_final_message()
        except (anthropic.BadRequestError, TypeError) as exc:
            if not use_schema:
                raise LLMError(f"Claude request failed: {exc}") from exc
            # This SDK or model does not accept output_config.format — fall back to
            # prompt-enforced JSON for this call and every later one.
            _structured_outputs_ok = False
            retry = dict(base)
            retry["output_config"] = {"effort": config.LLM_EFFORT}
            retry["system"] = system + _json_instruction(schema)
            with _client(secret).messages.stream(**retry) as stream:
                message = stream.get_final_message()
    except anthropic.APIStatusError as exc:
        # The SDK reports the status rather than handing back a response, so the
        # keyring is told from the exception instead of from a status line.
        held = keys.penalise("anthropic", secret,
                             status=getattr(exc, "status_code", None), body=str(exc)[:400])
        raise Refused(f"Claude refused the request: {exc}", secret, held) from exc
    keys.bless("anthropic", secret)

    if getattr(message, "stop_reason", None) == "refusal":
        raise LLMError(
            "Claude declined this topic. Try rephrasing it or choosing a different subject."
        )
    return "".join(b.text for b in message.content if getattr(b, "type", "") == "text")


# ── Gemini ────────────────────────────────────────────────────────────────────

_GEMINI_TYPES = {
    "object": "OBJECT",
    "array": "ARRAY",
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
}


def _to_gemini_schema(schema: Any) -> Any:
    """Convert a JSON Schema to Gemini's OpenAPI-flavoured subset.

    Gemini rejects `additionalProperties` outright and wants uppercase type
    names; `propertyOrdering` keeps generated fields in the order we declared
    them, which measurably improves adherence on nested objects.
    """
    if not isinstance(schema, dict):
        return schema

    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, str):
            out["type"] = _GEMINI_TYPES.get(value.lower(), value.upper())
        elif key == "properties" and isinstance(value, dict):
            out["properties"] = {k: _to_gemini_schema(v) for k, v in value.items()}
            out.setdefault("propertyOrdering", list(value.keys()))
        elif key == "items":
            out["items"] = _to_gemini_schema(value)
        else:
            out[key] = value
    return out


async def _gemini_call(
    client: httpx.AsyncClient, model: str, system: str, user: str,
    schema: dict | None, max_tokens: int,
    images: list[tuple[bytes, str]] | None = None, secret: str = "",
) -> httpx.Response:
    generation: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": 0.9}
    if schema:
        generation["responseMimeType"] = "application/json"
        generation["responseSchema"] = _to_gemini_schema(schema)

    parts: list[dict[str, Any]] = [
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
        for data, mime in (images or [])
    ]
    parts.append({"text": user})

    return await client.post(
        f"{config.GEMINI_BASE}/models/{model}:generateContent",
        headers={"x-goog-api-key": secret or config.key("gemini"),
                 "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation,
        },
    )


async def _gemini(system: str, user: str, schema: dict | None, max_tokens: int,
                  images: list[tuple[bytes, str]] | None = None) -> str:
    secret = config.key("gemini")
    if not secret:
        raise LLMError("Gemini kaliti yo'q — kutubxonadan qo'shing yoki "
                       "GEMINI_API_KEY ni sozlang.")

    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        # One key for the whole ladder below: the fallbacks are about the model
        # not accepting the request, so changing key mid-ladder would confuse
        # "this model won't do it" with "this key can't right now".
        resp = await _gemini_call(
            client, config.model("gemini_text"), system, user, schema, max_tokens,
            images, secret
        )

        if resp.status_code in (400, 404) and config.model("gemini_text_fallback"):
            # Unknown model name, or a schema this model won't accept.
            resp = await _gemini_call(
                client, config.model("gemini_text_fallback"), system, user, schema,
                max_tokens, images, secret
            )
            if resp.status_code >= 400 and schema:
                # Last resort: no response schema, ask for JSON in the prompt.
                resp = await _gemini_call(
                    client,
                    config.model("gemini_text_fallback"),
                    system + _json_instruction(schema),
                    user,
                    None,
                    max_tokens,
                    images,
                    secret,
                )

        if resp.status_code >= 400:
            held = keys.penalise("gemini", secret, status=resp.status_code,
                                 body=resp.text[:400])
            raise Refused(f"Gemini error {resp.status_code}: {resp.text[:400]}",
                          secret, held)
        keys.bless("gemini", secret)

        payload = resp.json()

    candidates = payload.get("candidates") or []
    if not candidates:
        blocked = (payload.get("promptFeedback") or {}).get("blockReason")
        raise LLMError(
            f"Gemini returned no content (blockReason={blocked})."
            if blocked
            else "Gemini returned no content."
        )

    candidate = candidates[0]
    text = "".join(
        part.get("text", "") for part in candidate.get("content", {}).get("parts", [])
    )
    if not text.strip() and candidate.get("finishReason") == "MAX_TOKENS":
        raise LLMError(
            "Gemini hit its output limit before producing anything. "
            "Try a shorter video or fewer scenes."
        )
    return text


# ── shared ────────────────────────────────────────────────────────────────────

def _json_instruction(schema: dict | None) -> str:
    return (
        "\n\nRespond with a single JSON value matching this JSON Schema exactly. "
        "Output raw JSON only — no prose, no markdown fences.\n" + json.dumps(schema)
    )


def _repair_truncated(text: str) -> Any | None:
    """Salvage what is complete out of an answer that stopped mid-sentence.

    A model that runs out of output tokens does not fail — it stops, often in the
    middle of a word, and the reply is valid JSON up to that point and nothing
    after. Throwing it away costs the user the six good items the model *did*
    finish, and hands them a wall of raw JSON as an error message.

    So the string is rewound to the last position where every bracket was closed
    and every string was terminated, the open containers are closed, and the
    result is parsed. Whatever was complete survives; the half-written item does
    not. If nothing at all is complete, this returns None and the caller reports
    a truncated answer honestly rather than a mangled one.
    """
    depth: list[str] = []
    in_string = False
    escaped = False
    safe = -1                       # last index where a value was cleanly closed

    for i, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if depth and depth[-1] == ch:
                depth.pop()
        # A complete element of an array or object ends at its comma, and the
        # value before that comma is the last thing known to be whole.
        if not in_string and ch == "," and len(depth) <= 2:
            safe = i

    if safe < 0 or not depth:
        return None
    candidate = text[:safe]         # drop the trailing comma and the half item
    # Close whatever is still open, innermost first.
    opened: list[str] = []
    in_string = False
    escaped = False
    for ch in candidate:
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            opened.append("}" if ch == "{" else "]")
        elif ch in "}]" and opened and opened[-1] == ch:
            opened.pop()
    try:
        return json.loads(candidate + "".join(reversed(opened)))
    except json.JSONDecodeError:
        return None


def _extract_json(text: str) -> Any:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the outermost JSON object/array in the response.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    # Nothing parsed whole. Before giving up, keep whatever the model finished
    # before it ran out of room — six good ideas beat an error message.
    repaired = _repair_truncated(text)
    if repaired is not None:
        return repaired
    raise LLMError(
        "Model javobi to'liq kelmadi — javob uzilib qolgan. Qaytadan urinib "
        f"ko'ring yoki kamroq narsa so'rang. Boshlanishi: {text[:200]}")


async def call_json(
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    max_tokens: int = 32000,
    images: list[tuple[bytes, str]] | None = None,
) -> Any:
    """Run one skill prompt and return parsed JSON.

    `images` is a list of `(bytes, mime)`. Both providers take pictures the same
    way as far as a caller here is concerned — as something to look at before
    reading the instruction — so a skill that needs to read a screenshot does not
    have to know which model is behind it.
    """
    provider = config.llm_provider()
    if not config.has_key(provider):
        raise LLMError(f"{provider} kaliti yo'q — kutubxonadan qo'shing.")

    # One try per key, at most. A key that has just refused is in cooldown, so
    # each pass through here reaches for a different one; the cap is what keeps a
    # provider that refuses everything from turning one script into ten calls.
    tries = max(1, min(keys.count(provider), 6))
    for attempt in range(tries):
        try:
            if provider == "anthropic":
                text = await asyncio.to_thread(
                    _claude_sync, system, user, schema, max_tokens, images)
            else:
                text = await _gemini(system, user, schema, max_tokens, images)
            return _extract_json(text)
        except Refused as exc:
            # Only a key that was actually set aside is worth walking away from.
            # A model that is simply down refuses every key, and asking the other
            # nine would spend them on the same answer.
            if attempt == tries - 1 or not exc.benched \
                    or not keys.can_switch(provider, exc.key):
                raise
    raise LLMError("The model could not be reached with any of the stored keys.")
