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

async def _flow(
    *, prompt: str, aspect: str, out_path: Path, job_id: str, scene: int,
    on_wait: Callable[[int, int], None] | None = None,
) -> Path:
    """Park the prompt and wait for somebody else's browser to answer it.

    This is not a provider in the sense the other three are — nothing is called,
    nothing is paid for, and no key is involved. The prompt goes into a queue,
    and a picture appears at `out_path` when whatever is watching that queue puts
    one there: a browser extension driving Google Flow in a tab you are already
    signed into, or you, with a file picker, on a phone.

    Which means the failure this has to handle well is *nobody is listening*. So
    the wait is bounded, the task is dropped when it expires, and the message
    says what was missing rather than reporting a provider error for a provider
    that was never contacted.
    """
    from .. import store  # local: store imports config, and config imports keys

    task = store.add_image_task(job_id=job_id, scene=scene, prompt=prompt,
                                aspect=aspect, out_path=str(out_path))
    task_id = task.get("id", "")
    waited = 0.0
    while waited < config.FLOW_PATIENCE:
        await asyncio.sleep(config.FLOW_POLL_SECONDS)
        waited += config.FLOW_POLL_SECONDS
        row = store.get_image_task(task_id)
        if row is None:  # cancelled from the outside
            raise ImageError("Rasm so'rovi bekor qilindi.")
        if row["status"] == "done" and out_path.exists():
            return out_path
        if row["status"] == "failed":
            raise ImageError(row.get("error") or "Flow'da rasm chiqmadi.")
        if on_wait and int(waited) % 15 == 0:
            left = len(store.list_image_tasks(job_id=job_id))
            on_wait(int(waited), left)

    store.finish_image_task(task_id, error="Kutish vaqti tugadi")
    raise ImageError(
        f"{max(1, round(config.FLOW_PATIENCE / 60))} daqiqa kutildi, rasm kelmadi. "
        "Kengaytma ishlayaptimi va Flow varag'i ochiqmi — tekshiring; "
        "yoki «Flow navbati» bo'limidan rasmni o'zingiz yuklang.")


# --- Flow Agent ---------------------------------------------------------------
#
# The same Google Flow subscription as `flow`, reached a different way. `flow`
# parks a prompt in a queue and waits for somebody to open a browser; this asks a
# backend for a picture and is handed one, so a video started from a phone at
# midnight builds itself without anybody being awake.
#
# Flow Agent (`kodelyx/flow-agent`) is a separate project and none of it lives
# here. What this speaks is its published HTTP API: upload a reference, ask for
# an image, take the bytes.

_FLOW_AGENT_SIZES = {
    "16:9": "1792x1024",
    "9:16": "1024x1792",
    "1:1": "1024x1024",
    "4:5": "1024x1280",
}

# Uploading a hero costs a round trip and returns an id that stays valid, so the
# same face is uploaded once per process rather than once per scene. Keyed by the
# file's bytes, not its path: a hero redrawn between scenes is a different
# reference even though it is the same file name.
_flow_agent_refs: dict[str, str] = {}


def _flow_agent_headers() -> dict[str, str]:
    # The key is optional at the far end — a backend started without SERVER_API_KEY
    # accepts anything — so an empty one means "no header", not "empty header".
    if not config.FLOW_AGENT_KEY:
        return {}
    return {"Authorization": f"Bearer {config.FLOW_AGENT_KEY}"}


# Google's uploader answers "Internal error encountered" to files it does not
# like, and gives no other clue. Big ones and ones carrying an alpha channel are
# the usual suspects, so the hero is sent as a plain RGB JPEG no larger than this
# on its longest side — plenty for a face reference, and a shape the endpoint has
# no reason to object to.
_HERO_EDGE = 1024


def _hero_upload_bytes(path: Path) -> tuple[bytes, str]:
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            if max(img.size) > _HERO_EDGE:
                img.thumbnail((_HERO_EDGE, _HERO_EDGE))
            buffer = io.BytesIO()
            img.save(buffer, format="JPEG", quality=92)
            return buffer.getvalue(), "image/jpeg"
    except Exception:  # noqa: BLE001 - an unreadable hero goes up as it is
        return path.read_bytes(), _mime_for(path)


async def _flow_agent_upload(client: httpx.AsyncClient, path: Path) -> str:
    """Put one hero photo in front of Flow, and remember what it was called."""
    import hashlib

    raw, mime = _hero_upload_bytes(path)
    fingerprint = hashlib.sha256(raw).hexdigest()
    known = _flow_agent_refs.get(fingerprint)
    if known:
        return known

    resp = await client.post(
        f"{config.FLOW_AGENT_URL}/v1/upload",
        headers=_flow_agent_headers(),
        json={"image_base64": f"data:{mime};base64,"
                              f"{base64.b64encode(raw).decode()}"},
    )
    if resp.status_code >= 400:
        raise ImageError(
            f"Flow Agent hero yuklashni rad etdi ({resp.status_code}): {resp.text[:200]}")
    media_id = (resp.json() or {}).get("media_id") or ""
    if not media_id:
        raise ImageError("Flow Agent hero uchun media_id qaytarmadi.")
    _flow_agent_refs[fingerprint] = media_id
    return media_id


async def _flow_agent(
    client: httpx.AsyncClient, prompt: str, references: list[Path], aspect: str,
    notes: list[str] | None = None,
) -> bytes:
    refs: list[str] = []
    for path in references[:10]:  # its own ceiling on how many faces one frame may carry
        try:
            refs.append(await _flow_agent_upload(client, path))
        except Exception as exc:  # noqa: BLE001 - the scene is worth more than the face
            # A hero that will not upload used to take the scene down with it,
            # and the retry re-uploaded the same rejected file three times before
            # settling for a grey placeholder. A picture drawn from the prompt
            # alone is worse than one with the right face and far better than no
            # picture at all — so it is drawn, and the loss is reported.
            if notes is not None:
                notes.append(f"«{path.stem}» hero yuklanmadi, u holda rasm heroisiz "
                             f"chizildi: {exc}")

    body: dict[str, object] = {
        "prompt": prompt,
        "model": config.FLOW_AGENT_MODEL,
        "n": 1,
        "size": _FLOW_AGENT_SIZES.get(aspect, "1024x1024"),
        # Bytes, not a link: the link is served by a backend that may be on
        # somebody's home machine, and this render is not.
        "response_format": "b64_json",
    }
    if refs:
        body["ref_media_ids"] = refs

    resp = await client.post(f"{config.FLOW_AGENT_URL}/v1/images/generations",
                             headers=_flow_agent_headers(), json=body)
    if resp.status_code >= 400:
        detail = resp.text[:300]
        if resp.status_code in {502, 503}:
            # Its own way of saying "no browser is connected", which is the one
            # failure the user can actually do something about.
            detail += " — Flow Agent kengaytmasi ulanganmi? Brauzer ochiqmi?"
        raise ImageError(f"Flow Agent error {resp.status_code}: {detail}")

    data = (resp.json() or {}).get("data") or []
    for item in data:
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        # It falls back to a URL when it could not keep the file itself.
        if item.get("url"):
            link = str(item["url"])
            if link.startswith("/"):
                link = f"{config.FLOW_AGENT_URL}{link}"
            got = await client.get(link, headers=_flow_agent_headers())
            if got.status_code < 400 and got.content:
                return got.content
    raise ImageError("Flow Agent rasm qaytarmadi.")


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
    job_id: str = "",
    scene: int = 0,
    on_wait: Callable[[int, int], None] | None = None,
) -> tuple[Path, str | None]:
    """Generate one scene image. Returns the path and a warning if it fell back.

    `IMAGE_DEADLINE` caps the whole attempt sequence. A provider that accepts the
    request and then goes quiet is worse than one that errors: without a ceiling,
    the retries hold this scene — and every scene queued behind it — for minutes.
    A placeholder now beats a perfect frame that never arrives.
    """
    provider = (provider or config.IMAGE_PROVIDER).lower()

    # Deliberately above the retry-and-deadline machinery below, not inside it.
    # That deadline bounds an HTTP call to a provider; this waits on a queue and
    # a browser, and retrying it would mean queueing the same prompt three times.
    if provider == "flow":
        try:
            return await _flow(prompt=prompt, aspect=aspect, out_path=out_path,
                               job_id=job_id, scene=scene, on_wait=on_wait), None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - same answer as every other provider
            # A placeholder and a warning, not an exception. One picture nobody
            # made must not take a hundred-scene render down with it — the scene
            # can be redrawn from the editor afterwards, and a video that is
            # ninety-nine per cent finished is worth having.
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(_placeholder(size, prompt))
            return out_path, f"Flow'dan rasm kelmadi, o'rniga vaqtinchalik rasm: {exc}"

    full_prompt = prompt
    if negative_prompt and provider in {"gemini", "openai", "flowagent"}:
        # These have no negative-prompt field, so fold it into the instruction.
        full_prompt = f"{prompt}\n\nDo not include: {negative_prompt}."

    last_error: Exception | None = None
    # Flow Agent is not an API in the same sense as the others: at the far end of
    # it is a browser, and a browser draws at the speed a browser draws. Holding
    # it to the timeout meant for an HTTP provider would time out every call.
    patience = (config.FLOW_AGENT_TIMEOUT if provider == "flowagent"
                else config.IMAGE_TIMEOUT)
    timeout = httpx.Timeout(patience, connect=30.0)
    # Things that went wrong without stopping the picture — a hero that would not
    # upload, say. Reported alongside a scene that did get drawn, because "it
    # worked" and "it worked without the face you asked for" are not the same
    # answer and only one of them needs looking at.
    notes: list[str] = []

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
                    elif provider == "flowagent":
                        payload = await _flow_agent(client, full_prompt, reference_paths,
                                                    aspect, notes)
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
        drawn = await asyncio.wait_for(run(), timeout=config.IMAGE_DEADLINE)
        # A picture, and — if something was lost getting there — what.
        return drawn, ("; ".join(dict.fromkeys(notes)) or None)
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
