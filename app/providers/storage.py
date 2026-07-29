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


async def ensure_bucket() -> None:
    """Create the public bucket once; an existing bucket is not an error."""
    if backend() != "supabase":
        return
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
        if resp.status_code >= 400 and resp.status_code not in (400, 409):
            raise StorageError(f"Could not create bucket: {resp.status_code} {resp.text[:200]}")


async def upload(local_path: Path, remote_path: str) -> str:
    """Upload a file and return a URL the browser can download from."""
    if backend() != "supabase":
        raise StorageError("Supabase storage is not configured.")

    content_type = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    url = f"{config.SUPABASE_URL}/storage/v1/object/{config.SUPABASE_BUCKET}/{remote_path}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(900.0, connect=30.0)) as client:
        resp = await client.post(
            url,
            headers={**_headers(content_type), "x-upsert": "true"},
            content=local_path.read_bytes(),
        )
        if resp.status_code >= 400:
            raise StorageError(f"Upload failed {resp.status_code}: {resp.text[:300]}")

    return f"{config.SUPABASE_URL}/storage/v1/object/public/{config.SUPABASE_BUCKET}/{remote_path}"


async def publish(local_path: Path, remote_path: str) -> tuple[str, str | None]:
    """Return (url, warning). Falls back to the local URL if the upload fails."""
    local_url = f"/api/files/{remote_path}"
    if backend() != "supabase":
        return local_url, None
    try:
        return await upload(local_path, remote_path), None
    except Exception as exc:  # noqa: BLE001 - the local file is still downloadable
        return local_url, f"Supabase upload failed, serving from this container instead: {exc}"


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
