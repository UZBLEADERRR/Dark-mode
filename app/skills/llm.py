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

from .. import config


class LLMError(RuntimeError):
    pass


# ── Claude ────────────────────────────────────────────────────────────────────

_anthropic_client = None
# Set once we learn the SDK/model rejects output_config, so we stop retrying it.
_structured_outputs_ok = True


def _client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _claude_sync(system: str, user: str, schema: dict | None, max_tokens: int,
                 images: list[tuple[bytes, str]] | None = None) -> str:
    global _structured_outputs_ok
    import anthropic

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
        with _client().messages.stream(**kwargs) as stream:
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
        with _client().messages.stream(**retry) as stream:
            message = stream.get_final_message()

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
    images: list[tuple[bytes, str]] | None = None,
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
        headers={"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": generation,
        },
    )


async def _gemini(system: str, user: str, schema: dict | None, max_tokens: int,
                  images: list[tuple[bytes, str]] | None = None) -> str:
    if not config.GEMINI_API_KEY:
        raise LLMError("GEMINI_API_KEY is not set.")

    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await _gemini_call(
            client, config.model("gemini_text"), system, user, schema, max_tokens, images
        )

        if resp.status_code in (400, 404) and config.model("gemini_text_fallback"):
            # Unknown model name, or a schema this model won't accept.
            resp = await _gemini_call(
                client, config.model("gemini_text_fallback"), system, user, schema,
                max_tokens, images
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
                )

        if resp.status_code >= 400:
            raise LLMError(f"Gemini error {resp.status_code}: {resp.text[:400]}")

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
    raise LLMError(f"The model did not return valid JSON. Got: {text[:400]}")


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
    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise LLMError("ANTHROPIC_API_KEY is not set.")
        text = await asyncio.to_thread(_claude_sync, system, user, schema, max_tokens, images)
    else:
        text = await _gemini(system, user, schema, max_tokens, images)
    return _extract_json(text)
