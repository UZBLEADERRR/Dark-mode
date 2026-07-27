"""Thin Claude wrapper shared by every skill.

Uses structured outputs when the installed SDK supports them, and degrades to
prompt-enforced JSON otherwise so the app still runs on an older `anthropic`.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import anthropic

from .. import config

_client: anthropic.Anthropic | None = None
# Set once we learn the SDK/model rejects output_config, so we stop retrying it.
_structured_outputs_ok = True


class LLMError(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if not config.ANTHROPIC_API_KEY:
        raise LLMError("ANTHROPIC_API_KEY is not set — the AI skills cannot run.")
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _extract_json(text: str) -> Any:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: grab the outermost JSON object/array in the response.
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Claude did not return valid JSON. Got: {text[:400]}")


def _text_of(message: Any) -> str:
    return "".join(
        block.text for block in message.content if getattr(block, "type", "") == "text"
    )


def _call_sync(
    system: str,
    user: str,
    schema: dict[str, Any] | None,
    max_tokens: int,
) -> Any:
    global _structured_outputs_ok

    kwargs: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }

    use_schema = bool(schema) and _structured_outputs_ok
    attempt_kwargs = dict(kwargs)
    if use_schema:
        attempt_kwargs["output_config"] = {
            "effort": config.LLM_EFFORT,
            "format": {"type": "json_schema", "schema": schema},
        }
    else:
        attempt_kwargs["output_config"] = {"effort": config.LLM_EFFORT}

    try:
        with client().messages.stream(**attempt_kwargs) as stream:
            message = stream.get_final_message()
    except (anthropic.BadRequestError, TypeError) as exc:
        if not use_schema:
            raise LLMError(f"Claude request failed: {exc}") from exc
        # The SDK or the model does not accept output_config.format here —
        # fall back to prompt-enforced JSON for this and every later call.
        _structured_outputs_ok = False
        retry = dict(kwargs)
        retry["output_config"] = {"effort": config.LLM_EFFORT}
        retry["system"] = (
            system
            + "\n\nRespond with a single JSON value that matches this JSON Schema exactly. "
            "Output raw JSON only — no prose, no markdown fences.\n"
            + json.dumps(schema)
        )
        with client().messages.stream(**retry) as stream:
            message = stream.get_final_message()

    if getattr(message, "stop_reason", None) == "refusal":
        raise LLMError(
            "Claude declined this topic. Try rephrasing it or choosing a different subject."
        )

    return _extract_json(_text_of(message))


async def call_json(
    system: str,
    user: str,
    schema: dict[str, Any] | None = None,
    max_tokens: int = 32000,
) -> Any:
    """Run a Claude call off the event loop and return parsed JSON."""
    return await asyncio.to_thread(_call_sync, system, user, schema, max_tokens)
