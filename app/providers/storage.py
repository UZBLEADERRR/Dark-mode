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
