"""Scene image generation, with one adapter per provider.

All three adapters accept the same thing — a prompt plus zero or more hero
reference photos — and return raw image bytes, so the pipeline never has to know
which provider is configured.
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
from pathlib import Path
from typing import Callable

import httpx
from PIL import Image, ImageDraw, ImageFilter

from .. import config, keys


class ImageError(RuntimeError):
    pass


class Refused(ImageError):
    """The provider answered, and said no. Carries the key it said no to.

    Worth telling apart from every other failure because it is the one kind the
    app can route around: another key has its own allowance, so the retry does
    not have to wait for this one to recover. `benched` says whether that is what
    happened — a provider's own 500 is nobody's key's fault, and rushing to the
    next key would spend the good keys on a problem they cannot fix.
    """

    def __init__(self, message: str, key: str = "", benched: float = 0.0) -> None:
        super().__init__(message)
        self.key = key
        self.benched = benched


def _check(resp: httpx.Response, label: str, provider: str, secret: str) -> None:
    """Record how this key did, then raise if the call was refused."""
    if resp.status_code < 400:
        keys.bless(provider, secret)
        return
    benched = keys.penalise(provider, secret, status=resp.status_code,
                            body=resp.text[:400])
    raise Refused(f"{label} error {resp.status_code}: {resp.text[:300]}",
                  secret, benched)


_OPENAI_SIZES = {
    "16:9": "1536x1024",
    "9:16": "1024x1536",
    "1:1": "1024x1024",
    "4:5": "1024x1536",
}
_FAL_RATIOS = {"16:9": "16:9", "9:16": "9:16", "1:1": "1:1", "4:5": "3:4"}


def _data_uri(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def _mime_for(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    return "image/png"


# --- Gemini ------------------------------------------------------------------

async def _gemini(
    client: httpx.AsyncClient, prompt: str, refs: list[Path], aspect: str
) -> bytes:
    secret = config.key("gemini")
    if not secret:
        raise ImageError("Gemini kaliti yo'q — kutubxonadan qo'shing yoki "
                        "GEMINI_API_KEY ni sozlang.")

    parts: list[dict] = [{"text": prompt}]
    for ref in refs:
        parts.append(
            {
                "inline_data": {
                    "mime_type": _mime_for(ref),
                    "data": base64.b64encode(ref.read_bytes()).decode(),
                }
            }
        )

    url = f"{config.GEMINI_BASE}/models/{config.model('gemini_image')}:generateContent"
    headers = {"x-goog-api-key": secret, "Content-Type": "application/json"}

    async def _post(with_image_config: bool) -> httpx.Response:
        generation: dict = {"responseModalities": ["IMAGE"]}
        if with_image_config:
            generation["imageConfig"] = {"aspectRatio": aspect}
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": generation}
        return await client.post(url, headers=headers, json=body)

    resp = await _post(config.GEMINI_USE_IMAGE_CONFIG)
    if resp.status_code == 400 and config.GEMINI_USE_IMAGE_CONFIG:
        # Older image models reject imageConfig; retry without it and crop later.
        resp = await _post(False)
    _check(resp, "Gemini image", "gemini", secret)

    payload = resp.json()
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            blob = part.get("inlineData") or part.get("inline_data")
            if blob and blob.get("data"):
                return base64.b64decode(blob["data"])
    raise ImageError(f"Gemini returned no image. Response: {str(payload)[:300]}")


# --- fal.ai ------------------------------------------------------------------

async def _fal(
    client: httpx.AsyncClient, prompt: str, refs: list[Path], aspect: str, size: tuple[int, int]
) -> bytes:
    secret = config.key("fal")
    if not secret:
        raise ImageError("fal.ai kaliti yo'q — kutubxonadan qo'shing yoki "
                        "FAL_KEY ni sozlang.")

    headers = {"Authorization": f"Key {secret}", "Content-Type": "application/json"}
    if refs:
        model = config.model("fal_image")
        body = {
            "prompt": prompt,
            "image_urls": [_data_uri(r) for r in refs],
            "aspect_ratio": _FAL_RATIOS.get(aspect, "16:9"),
            "num_images": 1,
        }
    else:
        model = config.model("fal_text2img")
        body = {
            "prompt": prompt,
            "image_size": {"width": size[0], "height": size[1]},
            "num_images": 1,
        }

    resp = await client.post(f"https://fal.run/{model}", headers=headers, json=body)
    _check(resp, "fal.ai", "fal", secret)

    images = resp.json().get("images") or []
    if not images:
        raise ImageError("fal.ai returned no images.")

    url = images[0].get("url", "")
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    download = await client.get(url)
    download.raise_for_status()
    return download.content


# --- OpenAI ------------------------------------------------------------------

async def _openai(
    client: httpx.AsyncClient, prompt: str, refs: list[Path], aspect: str
) -> bytes:
    secret = config.key("openai")
    if not secret:
        raise ImageError("OpenAI kaliti yo'q — kutubxonadan qo'shing yoki "
                        "OPENAI_API_KEY ni sozlang.")

    headers = {"Authorization": f"Bearer {secret}"}
    size = _OPENAI_SIZES.get(aspect, "1536x1024")

    if refs:
        files = [
            ("image[]", (ref.name, ref.read_bytes(), _mime_for(ref)))
            for ref in refs[:4]
        ]
        data = {"model": config.model("openai_image"), "prompt": prompt, "size": size, "n": "1"}
        resp = await client.post(
            f"{config.OPENAI_BASE}/images/edits", headers=headers, data=data, files=files
        )
    else:
        resp = await client.post(
            f"{config.OPENAI_BASE}/images/generations",
            headers={**headers, "Content-Type": "application/json"},
            json={"model": config.model("openai_image"), "prompt": prompt, "size": size, "n": 1},
        )

    _check(resp, "OpenAI image", "openai", secret)

    items = resp.json().get("data") or []
    if not items:
        raise ImageError("OpenAI returned no images.")
    entry = items[0]
    if entry.get("b64_json"):
        return base64.b64decode(entry["b64_json"])
    if entry.get("url"):
        download = await client.get(entry["url"])
        download.raise_for_status()
        return download.content
    raise ImageError("OpenAI image response contained no data.")


# --- fallback ----------------------------------------------------------------

def _placeholder(size: tuple[int, int], seed: str) -> bytes:
    """A soft gradient so one failed scene cannot sink an otherwise good render."""
    rng = random.Random(seed)
    width, height = size
    top = (rng.randint(10, 70), rng.randint(10, 70), rng.randint(40, 110))
    bottom = (rng.randint(90, 180), rng.randint(60, 140), rng.randint(80, 160))

    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        draw.line(
            [(0, y), (width, y)],
            fill=tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)),
        )
    image = image.filter(ImageFilter.GaussianBlur(radius=max(width, height) / 220))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# The instruction that makes a picture keyable. The flat field has to be
# *flat* — a gradient or a cast shadow survives the key and arrives as a grey
# smear around the character — so it is said three ways.
CUTOUT_INSTRUCTION = (
    "Full body, head to feet, the subject alone and complete, centred with clear "
    "empty margin on all four sides — the figure must not touch any edge of the "
    "frame. The background must be one absolutely flat solid magenta #FF00FF "
    "fill: no gradient, no texture, no vignette, no ground, and no shadow of any "
    "kind cast onto it. Do not let magenta appear anywhere on the subject itself. "
    "Draw the character described here, not the reference image's layout: the "
    "reference is only for the character's face, hair, clothing and colours. "
    "Never reproduce a character sheet, a colour palette, a logo, a name label, "
    "expression grids, or any text."
)

CUTOUT_NEGATIVE = (
    "background scenery, floor, ground, shadow, drop shadow, reflection, "
    "gradient background, textured background, vignette, frame, border, text, "
    "watermark, multiple characters, cropped limbs, character sheet, model sheet, "
    "turnaround, expression sheet, colour palette, logo, name label, infographic, "
    "icons, collage, multiple poses, figure touching the frame edge"
)


# --- public API --------------------------------------------------------------

async def generate_image(
    *,
    prompt: str,
    negative_prompt: str,
    reference_paths: list[Path],
    aspect: str,
    size: tuple[int, int],
    provider: str | None = None,
    out_path: Path,
    attempts: int = 3,
    on_retry: Callable[[int, Exception], None] | None = None,
) -> tuple[Path, str | None]:
    """Generate one scene image. Returns the path and a warning if it fell back.

    `IMAGE_DEADLINE` caps the whole attempt sequence. A provider that accepts the
    request and then goes quiet is worse than one that errors: without a ceiling,
    the retries hold this scene — and every scene queued behind it — for minutes.
    A placeholder now beats a perfect frame that never arrives.
    """
    provider = (provider or config.IMAGE_PROVIDER).lower()
    full_prompt = prompt
    if negative_prompt and provider in {"gemini", "openai"}:
        # These two have no negative-prompt field, so fold it into the instruction.
        full_prompt = f"{prompt}\n\nDo not include: {negative_prompt}."

    last_error: Exception | None = None
    timeout = httpx.Timeout(config.IMAGE_TIMEOUT, connect=30.0)

    async def run() -> Path:
        nonlocal last_error
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(attempts):
                try:
                    if provider == "gemini":
                        payload = await _gemini(client, full_prompt, reference_paths, aspect)
                    elif provider == "fal":
                        body = prompt
                        if negative_prompt:
                            body = f"{prompt}"
                        payload = await _fal(client, body, reference_paths, aspect, size)
                    elif provider == "openai":
                        payload = await _openai(client, full_prompt, reference_paths, aspect)
                    else:
                        raise ImageError(f"Unknown image provider '{provider}'.")

                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_bytes(payload)
                    # Normalise to RGB PNG so ffmpeg never trips on CMYK or alpha.
                    with Image.open(out_path) as img:
                        img.convert("RGB").save(out_path, format="PNG")
                    return out_path
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - retried, then reported
                    last_error = exc
                    if attempt < attempts - 1:
                        if on_retry:
                            on_retry(attempt + 1, exc)
                        # The refused key is already cooling, so the next attempt
                        # picks a different one — there is nothing to wait for.
                        if not (isinstance(exc, Refused) and exc.benched
                                and keys.can_switch(provider, exc.key)):
                            await asyncio.sleep(2 * (attempt + 1))
        raise ImageError(str(last_error))

    try:
        return await asyncio.wait_for(run(), timeout=config.IMAGE_DEADLINE), None
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        reason = f"no answer within {config.IMAGE_DEADLINE:.0f}s"
        if last_error:
            reason += f" (last error: {last_error})"
    except Exception as exc:  # noqa: BLE001 - reported as a placeholder below
        reason = str(exc)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(_placeholder(size, prompt))
    return out_path, f"Image generation failed, used a placeholder: {reason}"
