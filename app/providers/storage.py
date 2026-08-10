"""Where finished videos live.

Railway's filesystem is ephemeral, so the default local backend is fine for a
quick test but loses everything on redeploy. With Supabase configured, the
render is uploaded and the download link keeps working across deploys.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

import httpx

from .. import config


class StorageError(RuntimeError):
    pass


def backend() -> str:
    if config.STORAGE_BACKEND == "supabase" and config.SUPABASE_URL and config.SUPABASE_SERVICE_KEY:
        return "supabase"
    return "local"


def _headers(content_type: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {config.SUPABASE_SERVICE_KEY}",
        "apikey": config.SUPABASE_SERVICE_KEY,
    }
    if content_type:
        headers["Content-Type"] = content_type
    return headers


# What the last attempt to provision the bucket found. A bucket that is not
# there is not a small problem — every picture and every voice clip goes past it
# — so the answer is remembered and reported rather than being swallowed by the
# background task that asked.
_bucket: dict[str, object] = {"ready": False, "problem": ""}


def bucket_problem() -> str:
    """Why object storage is not usable, in words, or "" when it is fine."""
    return str(_bucket["problem"]) if backend() == "supabase" else ""


def _bucket_missing(status: int, body: str) -> bool:
    """True when the server is saying the bucket itself does not exist."""
    low = body.lower()
    return status == 404 or "nosuchbucket" in low or "bucket not found" in low


def _why(status: int, body: str) -> str:
    """A refusal, said in a way that names the thing to go and change."""
    low = body.lower()
    if status in (401, 403) or "row-level security" in low or "unauthorized" in low:
        return ("SUPABASE_SERVICE_KEY bucket yaratishga ruxsat bermadi — bu "
                "odatda anon (publishable) kalit qo'yilganini bildiradi. "
                "Supabase → Project Settings → API → service_role kalitini oling, "
                "yoki Storage bo'limida "
                f"«{config.SUPABASE_BUCKET}» nomli public bucket'ni qo'lda yarating.")
    if "invalid" in low and "name" in low:
        return (f"«{config.SUPABASE_BUCKET}» bucket nomi qabul qilinmadi — "
                "SUPABASE_BUCKET'ni faqat kichik harf va chiziqchadan tuzing.")
    return f"Bucket yaratilmadi ({status}): {body[:200]}"


async def _bucket_exists(client: httpx.AsyncClient) -> bool:
    """Ask the server directly. Creation being refused is not the same as the
    bucket being absent: a key that may write objects but may not create buckets
    hits an existing bucket perfectly well."""
    try:
        resp = await client.get(
            f"{config.SUPABASE_URL}/storage/v1/bucket/{config.SUPABASE_BUCKET}",
            headers=_headers())
        return resp.status_code < 400
    except Exception:  # noqa: BLE001 - treated as "cannot confirm"
        return False


async def ensure_bucket(*, force: bool = False) -> bool:
    """Create the public bucket once. Returns True when it is usable.

    Never raises: a bucket that cannot be made is a configuration problem to
    report on the settings page, not a reason for the render to stop — the
    database keeps the files in the meantime.
    """
    if backend() != "supabase":
        return False
    if _bucket["ready"] and not force:
        return True
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{config.SUPABASE_URL}/storage/v1/bucket",
                headers=_headers("application/json"),
                json={
                    "name": config.SUPABASE_BUCKET,
                    "id": config.SUPABASE_BUCKET,
                    "public": True,
                    "file_size_limit": 1024 * 1024 * 1024,
                },
            )
            body = resp.text or ""
            if resp.status_code < 400 or "already exists" in body.lower():
                _bucket.update(ready=True, problem="")
                return True
            # Refused. The bucket may still be there and writable, so look
            # before deciding that nothing can be saved.
            if await _bucket_exists(client):
                _bucket.update(ready=True, problem="")
                return True
            _bucket.update(ready=False, problem=_why(resp.status_code, body))
            return False
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        _bucket.update(ready=False, problem=f"Supabase Storage javob bermadi: {exc}")
        return False


async def upload(local_path: Path, remote_path: str) -> str:
    """Upload a file and return a URL the browser can download from.

    A missing bucket is fixed here rather than reported: provisioning happens in
    the background at startup, and if that ever loses its race — or the bucket is
    deleted while the app is running — every upload after it would otherwise fail
    with "Bucket not found" until somebody redeployed.
    """
    if backend() != "supabase":
        raise StorageError("Supabase storage is not configured.")

    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{remote_path}"
    payload = local_path.read_bytes()

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
        async def send() -> httpx.Response:
            return await client.post(
                url,
                headers={**_headers(content_type), "x-upsert": "true"},
                content=payload,
            )

        resp = await send()
        if resp.status_code >= 400 and _bucket_missing(resp.status_code, resp.text or ""):
            if await ensure_bucket(force=True):
                resp = await send()
        if resp.status_code >= 400:
            body = resp.text or ""
            if _bucket_missing(resp.status_code, body) and _bucket["problem"]:
                raise StorageError(str(_bucket["problem"]))
            raise StorageError(f"Upload failed {resp.status_code}: {body[:300]}")
        _bucket.update(ready=True, problem="")

    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_BUCKET}/{remote_path}"


async def publish(local_path: Path, remote_path: str) -> tuple[str, str | None]:
    """Return (url, warning). Falls back to the local URL if the upload fails."""
    local_url = f"/api/files/{remote_path}"
    if backend() != "supabase":
        return local_url, None
    try:
        return await upload(local_path, remote_path), None
    except Exception as exc:  # noqa: BLE001 - the local file is still downloadable
        return local_url, (f"Supabase Storage qabul qilmadi: {exc} — video hozir "
                           "yuklab olinadi, lekin keyingi deploygacha. Yuqoridagi "
                           "sababni to'g'rilab, «Render»ni qayta bosing.")


# --- keeping the work in progress ---------------------------------------------
# A finished video is uploaded because it is the deliverable. Everything made on
# the way there — every scene picture, every line of voice-over — is uploaded for
# a different reason: it was paid for. A container that restarts halfway through
# a fifty-scene project used to come back with the scene list intact and every
# picture gone, which is the most expensive way for a render to fail.

def key_for(local_path: Path, root: Path) -> str | None:
    """The remote name for a project file, derived from where it sits locally."""
    try:
        return Path(local_path).resolve().relative_to(Path(root).resolve()).as_posix()
    except (ValueError, OSError):
        return None


async def mirror(local_path: Path, remote_path: str) -> bool:
    """Best-effort copy to object storage. Never raises: the file is still here."""
    if backend() != "supabase" or not local_path.exists():
        return False
    try:
        await upload(local_path, remote_path)
        return True
    except Exception:  # noqa: BLE001 - a failed mirror is not a failed render
        return False


PAGE = 1000     # how many entries one `list` call is asked for
BATCH = 500     # how many objects one `delete` call is given
MAX_DEPTH = 8   # how far down a project folder is followed


async def _every_object(client: httpx.AsyncClient, prefix: str) -> list[str]:
    """Every real object under this prefix, however deep it sits.

    `list` is one level deep and answers with folder rows — a name and no id —
    rather than with what is inside them. A project's pictures live at
    `<job>/images/...` and its voice at `<job>/audio/...`, so a single call sees
    two folders and the finished video, and a delete built from that removes the
    video and leaves every picture and every voice clip exactly where they were.
    """
    url = f"{config.SUPABASE_URL}/storage/v1/object/list/{config.SUPABASE_BUCKET}"
    found: list[str] = []
    seen: set[str] = set()
    todo: list[tuple[str, int]] = [(prefix, 0)]

    while todo:
        here, depth = todo.pop()
        if here in seen or depth > MAX_DEPTH:
            continue
        seen.add(here)
        offset, before = 0, len(found)
        while True:
            resp = await client.post(
                url, headers=_headers("application/json"),
                json={"prefix": here, "limit": PAGE, "offset": offset},
            )
            if resp.status_code >= 400:
                break
            rows = resp.json() or []
            for row in rows:
                name = row.get("name")
                if not name:
                    continue
                full = f"{here}/{name}"
                # A file carries an id, or at least the metadata that describes
                # its bytes. Anything with neither is a folder to walk into.
                if row.get("id") or row.get("metadata"):
                    found.append(full)
                else:
                    todo.append((full, depth + 1))
            if len(rows) < PAGE:
                break
            offset += PAGE
        if depth and len(found) == before:
            # Walked into it and found nothing. Either it is an empty folder, in
            # which case deleting it is a no-op, or it is a file this deployment
            # described without an id — and that one has to go. Cheap either way.
            found.append(here)
    return found


async def project_folders() -> list[str]:
    """The project folders the bucket is holding, whether or not we still know them.

    A project deleted before the bucket sweep was recursive left its pictures
    and its voice-over behind, and nothing since has ever looked at them. This
    is how they are found: the bucket's own answer to "what is in here", rather
    than our list of what ought to be.
    """
    if backend() != "supabase":
        return []
    url = f"{config.SUPABASE_URL}/storage/v1/object/list/{config.SUPABASE_BUCKET}"
    names: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=30.0)) as client:
            offset = 0
            while True:
                resp = await client.post(
                    url, headers=_headers("application/json"),
                    json={"prefix": "", "limit": PAGE, "offset": offset},
                )
                if resp.status_code >= 400:
                    break
                rows = resp.json() or []
                names += [row["name"] for row in rows
                          if row.get("name", "").startswith("job_")]
                if len(rows) < PAGE:
                    break
                offset += PAGE
    except Exception:  # noqa: BLE001 - a bucket that cannot be reached has nothing to say
        return names
    return names


async def remove_folder(prefix: str) -> int:
    """Delete everything stored under one project. Returns how many files went.

    Deleting a project used to remove the row, the local folder and the database
    copies — and leave the bucket exactly as it was, including the finished video
    at a public URL. "Deleted" has to mean deleted, or the word is doing harm:
    somebody removes a video precisely because they do not want it reachable, and
    the one copy on the open internet is the one that survived.

    Never raises. A bucket that cannot be reached is worth reporting, but not
    worth refusing to delete the project over — the local copies going is still
    better than nothing going.
    """
    if backend() != "supabase" or not prefix.strip("/"):
        return 0
    prefix = prefix.strip("/")
    base = f"{config.SUPABASE_URL}/storage/v1/object"
    gone = 0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            keys = await _every_object(client, prefix)
            for start in range(0, len(keys), BATCH):
                batch = keys[start:start + BATCH]
                resp = await client.request(
                    "DELETE", f"{base}/{config.SUPABASE_BUCKET}",
                    headers=_headers("application/json"),
                    json={"prefixes": batch},
                )
                if resp.status_code >= 400:
                    continue
                # It answers with what it actually removed. Counted from that
                # rather than from what was asked: a number that says nine when
                # two went is worse than no number, because it reads as proof.
                try:
                    removed = resp.json()
                except ValueError:
                    removed = None
                gone += len(removed) if isinstance(removed, list) else len(batch)
    except Exception:  # noqa: BLE001 - a delete that cannot reach the bucket
        return gone
    return gone


async def fetch(remote_path: str, local_path: Path) -> bool:
    """Pull a file back down. Used when a redeploy left the disk empty."""
    if backend() != "supabase":
        return False
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{remote_path}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=20.0)) as client:
            resp = await client.get(url, headers=_headers())
            if resp.status_code >= 400:
                return False
            local_path.parent.mkdir(parents=True, exist_ok=True)
            # Written beside the target and moved into place, so a download that
            # dies halfway cannot leave a truncated image to be served as real.
            partial = local_path.with_name(local_path.name + ".part")
            partial.write_bytes(resp.content)
            partial.replace(local_path)
            return True
    except Exception:  # noqa: BLE001 - the caller reports a plain 404
        return False
