"""Your own API keys, several per provider, used in turn.

The problem this solves is a per-minute allowance. One Gemini key reads ten lines
a minute; a fifty-scene video therefore spends five minutes waiting for a limit
rather than for work. Ten keys read a hundred lines a minute, and the app moves
to the next one the moment one refuses — which is the whole point, and the reason
the interesting function here is `penalise` rather than `pick`.

Three rules the rest of the app depends on:

**A refusal is remembered.** A key that has just been rate-limited goes into a
cooldown and is skipped until it lifts, rather than being tried again a
millisecond later and refused again. Cooldowns are stored, so a restart does not
forget which key is busy.

**Keys are used in turn, not in order.** Round-robin, so ten keys spread the load
instead of the first one absorbing everything and the other nine idling.

**The environment still works.** A deployment that has only `GEMINI_API_KEY` set
behaves exactly as it did: the env value is the last resort when the keyring is
empty, and it can be told apart from a stored key because it has no id.
"""

from __future__ import annotations

import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from . import config, store

# Which providers can hold keys here, and which environment variable each one
# falls back to.
PROVIDERS: dict[str, str] = {
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "fal": "FAL_KEY",
}

# How long a refused key sits out. A per-minute limit lifts on its own within the
# minute; a spent daily quota does not, and trying it every minute for the rest of
# the day is just noise.
RATE_LIMIT_COOLDOWN = 65.0
QUOTA_COOLDOWN = 30 * 60.0
# A key the provider says is wrong will still be wrong in an hour. It is not
# disabled — that is the user's decision, and a key can be rejected for reasons
# that pass — but it goes to the back of the queue for a long time.
BAD_KEY_COOLDOWN = 60 * 60.0

# Where the round robin is up to, per provider. In memory on purpose: it is a
# fairness hint, not a fact worth a database write per call.
_turn: dict[str, int] = {}
_lock = threading.Lock()


def clean(secret: str) -> str:
    """A pasted key, as the provider needs to receive it.

    Copying a key on a phone is lossy in ways that have nothing to do with the
    key: the dashboard wraps it, so the paste carries newlines inside it; a
    long-press copy takes the surrounding quotes; a keyboard adds a trailing
    space. All of that is removed here, once, so no adapter has to think about it.

    What is *not* done here is judging the key. There is no minimum length and no
    expected prefix — Google issues both `AIza…` and `AQ.…`, of different lengths,
    and a rule about shape could only ever reject a real key.
    """
    text = (secret or "").strip()
    # Opening and closing are not always the same character — a phone turns a
    # pair of straight quotes into “ … ”, so this checks the pair, not equality.
    quotes = {"\"": "\"", "'": "'", "`": "`", "‘": "’", "“": "”", "«": "»"}
    while len(text) > 1 and quotes.get(text[0]) == text[-1]:
        text = text[1:-1].strip()
    # Characters with no width and no meaning: a zero-width space, a soft hyphen
    # left behind by a wrapped line, the BOM a text editor writes, the bidi marks
    # an Arabic or Hebrew keyboard adds around Latin text. None of them survive a
    # round trip through the provider, and none of them are visible on screen — so
    # a key that carries one looks perfect and is rejected, which is the worst
    # combination there is. `Cf` is the format category, `Cc` the control one.
    text = "".join(c for c in text if unicodedata.category(c) not in ("Cf", "Cc"))
    # Every kind of space, not just the ends: a wrapped paste has them inside.
    return "".join(text.split())


def unsendable(secret: str) -> str:
    """Why this key cannot even be put in a header, or "" if it can.

    A key is an HTTP header value, and a header is ASCII. A Cyrillic `А` pasted in
    place of a Latin `A` — which is what a phone keyboard left in Russian layout
    produces, and which no amount of staring at the screen will reveal — makes the
    request fail inside the HTTP client with an encoding error, long before any
    provider sees it. Catching it here means the answer names the character
    instead of quoting a stack trace.
    """
    for i, ch in enumerate(secret or ""):
        if ord(ch) > 127:
            name = unicodedata.name(ch, f"U+{ord(ch):04X}")
            return (f"Kalitning {i + 1}-belgisi lotincha emas: «{ch}» ({name}). "
                    "Ehtimol boshqa klaviaturadan tushib qolgan — kalitni "
                    "brauzerdan qaytadan nusxalang.")
    return ""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def cooling(row: dict[str, Any], now: datetime | None = None) -> float:
    """Seconds left on this key's cooldown, or 0."""
    until = _parse(row.get("cooldown_until") or "")
    if until is None:
        return 0.0
    left = (until - (now or _now())).total_seconds()
    return left if left > 0 else 0.0


def env_secret(provider: str) -> str:
    return getattr(config, PROVIDERS.get(provider, ""), "") or ""


def stored(provider: str) -> list[dict[str, Any]]:
    """Every stored key for this provider, secrets included. Never leaves here."""
    try:
        return store.key_secrets(provider)
    except Exception:  # noqa: BLE001 - a database blip must not lose the env key
        return []


def usable(provider: str) -> list[dict[str, Any]]:
    """Keys that could be used right now, in the order to try them.

    Cooling keys are not dropped, they are moved to the back: when every key is
    cooling, the least-cool one is still better than giving up — and giving up on
    a fifty-scene video because every key is busy for another twenty seconds
    would be the wrong answer.
    """
    now = _now()
    rows = [r for r in stored(provider) if r.get("enabled") and r.get("secret")]
    if not rows:
        secret = env_secret(provider)
        return [{"id": "", "provider": provider, "label": "muhitdan (.env)",
                 "secret": secret, "enabled": True, "cooldown_until": ""}] if secret else []

    ready = [r for r in rows if not cooling(r, now)]
    if ready:
        with _lock:
            at = _turn.get(provider, 0) % len(ready)
            _turn[provider] = at + 1
        return ready[at:] + ready[:at]
    # All cooling: soonest first, so the wait is as short as it can be.
    return sorted(rows, key=lambda r: cooling(r, now))


def pick(provider: str) -> str:
    """The one secret to use for the next call, or '' when there is none."""
    options = usable(provider)
    return str(options[0]["secret"]) if options else ""


def count(provider: str) -> int:
    """How many keys could serve this provider at all — enabled, cooling or not."""
    rows = [r for r in stored(provider) if r.get("enabled") and r.get("secret")]
    return len(rows) if rows else (1 if env_secret(provider) else 0)


def ready(provider: str) -> int:
    """How many are usable this second. Zero means every one is cooling."""
    rows = [r for r in stored(provider) if r.get("enabled") and r.get("secret")]
    if not rows:
        return 1 if env_secret(provider) else 0
    now = _now()
    return sum(1 for r in rows if not cooling(r, now))


def have(provider: str) -> bool:
    return count(provider) > 0


def _row_for(provider: str, secret: str) -> dict[str, Any] | None:
    if not secret:
        return None
    return next((r for r in stored(provider) if r.get("secret") == secret), None)


def bless(provider: str, secret: str) -> None:
    """It worked. Clears any cooldown and the remembered error."""
    row = _row_for(provider, secret)
    if row and row.get("id"):
        try:
            store.bump_key(row["id"], ok=True, when=_stamp(_now()))
        except Exception:  # noqa: BLE001 - bookkeeping is never worth a failure
            pass


def classify(status: int | None, body: str) -> tuple[float, str]:
    """How long to sit this key out, and what to remember about why.

    The distinction that matters is between *busy* and *wrong*. A busy key is
    worth coming back to within the minute; a wrong one is not, and telling them
    apart is the difference between rotating usefully and hammering a dead key.
    """
    low = (body or "").lower()
    if status == 429 or "resource_exhausted" in low or "rate limit" in low:
        if "quota" in low and "per minute" not in low and "per-minute" not in low:
            return QUOTA_COOLDOWN, "Kunlik kvota tugagan"
        return RATE_LIMIT_COOLDOWN, "Daqiqalik limitga urildi"
    if status in (401, 403) or "api key not valid" in low or "invalid_api_key" in low \
            or "permission_denied" in low or "unauthorized" in low:
        return BAD_KEY_COOLDOWN, "Kalit qabul qilinmadi — noto'g'ri yoki ruxsati yo'q"
    if status == 400 and "api key" in low:
        return BAD_KEY_COOLDOWN, "Kalit qabul qilinmadi"
    return 0.0, ""


def penalise(provider: str, secret: str, *, status: int | None = None,
             body: str = "", seconds: float | None = None) -> float:
    """Sit this key out. Returns the cooldown applied, in seconds.

    Zero means the failure was not the key's fault — a timeout, a model that
    returned nothing — and rotating away from a working key for that would spend
    the good keys on a problem they cannot fix.
    """
    row = _row_for(provider, secret)
    hold, why = classify(status, body)
    if seconds is not None:
        hold = seconds
        why = why or "Vaqtincha chetlab o'tildi"
    if hold <= 0:
        return 0.0
    if row and row.get("id"):
        try:
            store.bump_key(row["id"], ok=False, when=_stamp(_now()),
                           cooldown_until=_stamp(_now() + timedelta(seconds=hold)),
                           error=f"{why}: {(body or '')[:160]}" if body else why)
        except Exception:  # noqa: BLE001
            pass
    return hold


def can_switch(provider: str, secret: str) -> bool:
    """Is there another key to move to instead of waiting for this one?

    This is the question the voice stage asks when it is rate-limited: with a
    second key, being throttled costs nothing and waiting would be a mistake.
    """
    now = _now()
    others = [r for r in stored(provider)
              if r.get("enabled") and r.get("secret") and r.get("secret") != secret]
    return any(not cooling(r, now) for r in others)


def health(provider: str) -> dict[str, Any]:
    """What the settings page shows about this provider's supply of keys."""
    rows = [r for r in stored(provider) if r.get("enabled") and r.get("secret")]
    now = _now()
    return {
        "provider": provider,
        "keys": len(rows) or (1 if env_secret(provider) else 0),
        "ready": ready(provider),
        "from_env": not rows and bool(env_secret(provider)),
        "cooling": [
            {"label": r.get("label") or "nomsiz", "seconds": round(cooling(r, now))}
            for r in rows if cooling(r, now)
        ],
    }


async def probe(provider: str, secret: str) -> tuple[bool, str, float]:
    """Ask the provider whether this one key works. Never raises.

    Worth having because the alternative way to find out is a failed video an
    hour later. Each provider is asked the cheapest question it answers — a model
    listing where there is one, a one-token completion where there is not — and a
    429 counts as working, since being throttled means the key was recognised.

    Returns `(ok, what to tell the user, how long to bench it)`. The cooldown is
    decided here rather than by the caller because only here is the provider's own
    words still to hand — a translated message classifies as nothing at all, and a
    key rejected by name would quietly stay in the rotation.
    """
    import httpx

    secret = clean(secret)
    if not secret:
        return False, "Kalit bo'sh", 0.0
    wrong = unsendable(secret)
    if wrong:
        # Not benched: the key on the dashboard may be perfectly good, and it is
        # the copy that is broken. Re-pasting is the fix, not waiting an hour.
        return False, wrong, 0.0
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0)) as client:
            if provider == "gemini":
                resp = await client.get(f"{config.GEMINI_BASE}/models?pageSize=1",
                                        headers={"x-goog-api-key": secret})
            elif provider == "openai":
                resp = await client.get(f"{config.OPENAI_BASE}/models",
                                        headers={"Authorization": f"Bearer {secret}"})
            elif provider == "elevenlabs":
                resp = await client.get("https://api.elevenlabs.io/v1/user",
                                        headers={"xi-api-key": secret})
            elif provider == "anthropic":
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={"x-api-key": secret, "anthropic-version": "2023-06-01",
                             "content-type": "application/json"},
                    json={"model": config.model("anthropic_text"), "max_tokens": 1,
                          "messages": [{"role": "user", "content": "hi"}]})
            elif provider == "fal":
                # fal publishes nothing to ask, so the only honest answer is
                # "unknown" — reporting a guess as a result would be worse.
                return True, "fal tekshirib bo'lmaydi — ishlatib ko'rish kerak", 0.0
            else:
                return False, f"Notanish provayder '{provider}'", 0.0
    except Exception as exc:  # noqa: BLE001 - a network blip is not a verdict
        return False, f"Ulanmadi: {str(exc)[:120]}", 0.0

    if resp.status_code == 429:
        return True, "Kalit ishlaydi, lekin hozir limitda", 0.0
    if resp.status_code < 400:
        return True, "Ishlaydi", 0.0
    hold, why = classify(resp.status_code, resp.text)
    if not why:
        # The provider failed rather than the key. Saying so, and not benching a
        # key that may be perfectly good, is the honest answer.
        return False, f"{resp.status_code}: {resp.text[:120]}", 0.0
    return False, why, hold


def mask(secret: str) -> str:
    """Enough to recognise a key by, not enough to use."""
    clean = (secret or "").strip()
    if len(clean) <= 8:
        return "•" * len(clean)
    return f"{clean[:4]}…{clean[-4:]}"


# Installed at import, so `config.key()` and `config.has_key()` are keyring-aware
# everywhere without config importing anything.
config.set_key_source(pick, count)
