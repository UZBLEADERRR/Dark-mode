"""Publishing to the user's own YouTube channel.

This is the one provider that is not an API key. It acts *as* the person, on
their channel, so it needs their consent — which means OAuth, a refresh token,
and that token living in the database rather than the environment: whoever grants
it is not whoever deployed the app.

Two things about YouTube's API are worth knowing before reading this:

  * An upload costs 1600 units of a 10,000-unit daily quota. That is six videos a
    day on a default project, and the seventh fails with a quota error rather
    than a helpful one — so that error is translated here.
  * An app that has not been through Google's verification can only ever upload
    as private, whatever it asks for. That is Google's rule, not a bug, and it is
    reported rather than silently ignored.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from .. import config, store

ACCOUNT_KEY = "youtube.account"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"
AUTH_HOST = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3"

PRIVACY = ("private", "unlisted", "public")

# One upload is a large file over a slow link; the read timeout has to allow for
# it, while a connect that has not answered in half a minute never will.
LONG = httpx.Timeout(1800.0, connect=30.0)
SHORT = httpx.Timeout(30.0, connect=15.0)


class YouTubeError(RuntimeError):
    pass


# --- what we know about the connected channel ---------------------------------

def account() -> dict[str, Any]:
    return store.get_setting(ACCOUNT_KEY) or {}


def connected() -> bool:
    return bool(account().get("refresh_token"))


def status() -> dict[str, Any]:
    """What the settings page shows: can we publish, and as whom."""
    acc = account()
    return {
        "configured": config.youtube_ready(),
        "connected": bool(acc.get("refresh_token")),
        "channel_title": acc.get("channel_title", ""),
        "channel_id": acc.get("channel_id", ""),
        "connected_at": acc.get("connected_at", ""),
        "redirect_uri": redirect_uri(),
        # Said up front rather than discovered on the seventh upload of the day.
        "note": _setup_note(),
    }


def _setup_note() -> str:
    if not config.youtube_ready():
        return ("YouTube'ga joylash uchun Google Cloud'da OAuth mijozi kerak: "
                "YOUTUBE_CLIENT_ID va YOUTUBE_CLIENT_SECRET qo'ying.")
    if not config.PUBLIC_URL:
        return ("PUBLIC_URL qo'yilmagan — Google brauzerni qaytaradigan manzil "
                "shundan tuziladi. Railway'da o'zi topiladi; boshqa joyda "
                "PUBLIC_URL=https://sizning-domeningiz deb yozing.")
    if not connected():
        return "Kanalingizni ulang — bir marta ruxsat berasiz, keyin o'zi joylaydi."
    return ("Kuniga taxminan 6 ta video joylash mumkin (YouTube kvotasi). "
            "Ilova Google tomonidan tasdiqlanmagan bo'lsa, videolar faqat "
            "«private» bo'lib chiqadi — bu Google qoidasi.")


def disconnect() -> None:
    store.set_setting(ACCOUNT_KEY, {})
    _token_cache.clear()


# --- the consent dance --------------------------------------------------------

def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/api/youtube/callback" if config.PUBLIC_URL else ""


def auth_url(state: str = "") -> str:
    """Where to send the browser. Raises when the app is not set up for it."""
    if not config.youtube_ready():
        raise YouTubeError(
            "YOUTUBE_CLIENT_ID va YOUTUBE_CLIENT_SECRET qo'yilmagan — "
            "Google Cloud → APIs & Services → Credentials → OAuth client ID.")
    if not redirect_uri():
        raise YouTubeError(
            "PUBLIC_URL qo'yilmagan, shuning uchun Google brauzerni qaytaradigan "
            "manzil aniqlanmadi. PUBLIC_URL=https://sizning-domeningiz qo'ying.")
    params = {
        "client_id": config.YOUTUBE_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPES,
        # Both are needed to be handed a refresh token: offline asks for one, and
        # consent forces the prompt on a re-connect that would otherwise be
        # answered from Google's memory — and answered without a refresh token.
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"{AUTH_HOST}?{httpx.QueryParams(params)}"


async def exchange(code: str) -> dict[str, Any]:
    """Turn the one-time code into a stored refresh token, and say whose it is."""
    async with httpx.AsyncClient(timeout=SHORT) as client:
        resp = await client.post(TOKEN_URL, data={
            "code": code,
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "redirect_uri": redirect_uri(),
            "grant_type": "authorization_code",
        })
    if resp.status_code >= 400:
        raise YouTubeError(_why(resp.status_code, resp.text))
    payload = resp.json()
    refresh = payload.get("refresh_token")
    if not refresh:
        # Google withholds it when the account has already granted this client and
        # the prompt was skipped. `prompt=consent` above is what prevents it.
        raise YouTubeError(
            "Google refresh token bermadi — bu odatda ruxsat allaqachon "
            "berilganini bildiradi. Google akkountingiz sozlamalarida bu ilovaga "
            "berilgan ruxsatni olib tashlab, qaytadan ulang.")

    who = await _channel(payload.get("access_token", ""))
    saved = {
        "refresh_token": refresh,
        "channel_id": who.get("id", ""),
        "channel_title": who.get("title", ""),
        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    store.set_setting(ACCOUNT_KEY, saved)
    _token_cache.clear()
    return {k: v for k, v in saved.items() if k != "refresh_token"}


async def _channel(token: str) -> dict[str, str]:
    if not token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=SHORT) as client:
            resp = await client.get(f"{API_URL}/channels",
                                    params={"part": "snippet", "mine": "true"},
                                    headers={"Authorization": f"Bearer {token}"})
        items = resp.json().get("items") or []
        if not items:
            return {}
        return {"id": items[0].get("id", ""),
                "title": (items[0].get("snippet") or {}).get("title", "")}
    except Exception:  # noqa: BLE001 - the name is a nicety, the token is the point
        return {}


# --- access tokens ------------------------------------------------------------
# Refreshed on demand and held for slightly less than Google's hour, so a batch
# of uploads is one refresh rather than one per file.

_token_cache: dict[str, Any] = {}


async def access_token() -> str:
    now = time.time()
    if _token_cache.get("token") and _token_cache.get("until", 0) > now:
        return str(_token_cache["token"])

    refresh = account().get("refresh_token")
    if not refresh:
        raise YouTubeError("YouTube kanali ulanmagan.")

    async with httpx.AsyncClient(timeout=SHORT) as client:
        resp = await client.post(TOKEN_URL, data={
            "refresh_token": refresh,
            "client_id": config.YOUTUBE_CLIENT_ID,
            "client_secret": config.YOUTUBE_CLIENT_SECRET,
            "grant_type": "refresh_token",
        })
    if resp.status_code >= 400:
        body = resp.text
        if "invalid_grant" in body:
            # The user revoked it, or it expired from disuse. Nothing to retry.
            raise YouTubeError(
                "YouTube ruxsati bekor qilingan yoki eskirgan — kanalni qaytadan ulang.")
        raise YouTubeError(_why(resp.status_code, body))

    payload = resp.json()
    token = payload.get("access_token") or ""
    _token_cache.update(token=token, until=now + max(60, int(payload.get("expires_in", 3600)) - 120))
    return token


# --- uploading ----------------------------------------------------------------

def _why(status_code: int, body: str) -> str:
    """A refusal, in words that name the thing to change."""
    low = (body or "").lower()
    if "quotaexceeded" in low or "dailylimitexceeded" in low:
        return ("YouTube kunlik kvotasi tugadi — standart loyihada kuniga ~6 ta "
                "video. Ertaga o'zi tiklanadi, yoki Google Cloud'da kvota "
                "oshirishni so'raysiz.")
    if "uploadlimitexceeded" in low:
        return "Bu kanal bugun juda ko'p video yukladi — YouTube vaqtincha to'xtatdi."
    if "youtubesignuprequired" in low:
        return "Bu Google akkountda YouTube kanali yo'q — avval kanal yarating."
    if "forbidden" in low and "upload" in low:
        return ("Bu akkountga video yuklash taqiqlangan — kanal tasdiqlanganini "
                "va ogohlantirish (strike) yo'qligini tekshiring.")
    if status_code in (401, 403):
        return ("YouTube ruxsat bermadi — kanalni qaytadan ulab ko'ring. "
                f"({status_code}: {body[:160]})")
    return f"YouTube xatosi {status_code}: {body[:240]}"


def explain(exc: Exception) -> str:
    return str(exc) if isinstance(exc, YouTubeError) else f"YouTube: {exc}"


async def upload(
    path: Path,
    *,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy: str = "private",
    publish_at: str | None = None,
    category_id: str = "22",
    language: str = "",
) -> dict[str, str]:
    """Put a finished video on the connected channel.

    `publish_at` is an RFC3339 instant. YouTube only honours it on a private
    video — so asking for one forces privacy to private, which is what makes
    "prepare it now, publish it Tuesday at nine" work without this app needing to
    be awake on Tuesday at nine.
    """
    if not path.exists():
        raise YouTubeError(f"Video fayli topilmadi: {path.name}")
    if privacy not in PRIVACY:
        privacy = "private"

    status_part: dict[str, Any] = {
        "privacyStatus": "private" if publish_at else privacy,
        # Required by YouTube since 2020; the app has no way to know, so it says
        # what is true of everything it makes.
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        status_part["publishAt"] = publish_at

    snippet: dict[str, Any] = {
        "title": (title or "Video")[:100],
        "description": (description or "")[:5000],
        "tags": [t[:60] for t in (tags or [])][:30],
        "categoryId": category_id,
    }
    if language:
        snippet["defaultLanguage"] = language
        snippet["defaultAudioLanguage"] = language

    token = await access_token()
    body = path.read_bytes()

    async with httpx.AsyncClient(timeout=LONG) as client:
        # Step one: announce the upload and be told where to put it.
        start = await client.post(
            UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(len(body)),
                "X-Upload-Content-Type": "video/*",
            },
            json={"snippet": snippet, "status": status_part},
        )
        if start.status_code >= 400:
            raise YouTubeError(_why(start.status_code, start.text))
        session = start.headers.get("location") or start.headers.get("Location")
        if not session:
            raise YouTubeError("YouTube yuklash manzilini bermadi.")

        # Step two: the bytes.
        sent = await client.put(
            session,
            headers={"Content-Type": "video/*", "Content-Length": str(len(body))},
            content=body,
        )
        if sent.status_code >= 400:
            raise YouTubeError(_why(sent.status_code, sent.text))

    made = sent.json()
    video_id = made.get("id") or ""
    if not video_id:
        raise YouTubeError("YouTube video id qaytarmadi.")
    got = (made.get("status") or {}).get("privacyStatus", "")
    return {
        "id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "privacy": got or status_part["privacyStatus"],
        # Said back rather than assumed: an unverified app is forced to private,
        # and a caller that asked for public needs to know it did not get it.
        "asked_privacy": privacy,
        "publish_at": publish_at or "",
    }


async def set_thumbnail(video_id: str, path: Path) -> bool:
    """Best effort: a video with the wrong thumbnail is still a published video."""
    if not path.exists():
        return False
    try:
        token = await access_token()
        async with httpx.AsyncClient(timeout=LONG) as client:
            resp = await client.post(
                "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
                params={"videoId": video_id},
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "image/png"},
                content=path.read_bytes(),
            )
        return resp.status_code < 400
    except Exception:  # noqa: BLE001 - never fails a publish
        return False
