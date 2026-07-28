"""A shared, per-minute ceiling on outbound provider calls.

Voice keys are usually sold by the minute rather than by the day: ten requests
a minute is a common free tier, and going over it earns a 429 rather than a
queue. Pacing ourselves is strictly better than being throttled — a refused
call still costs a round trip, and the retry that follows arrives into the same
full window.

The window slides: each grant records its moment, and the eleventh caller in a
minute waits exactly long enough for the oldest of the ten to fall out the back.
A 429 that slips through anyway can be reported with `penalise()`, which holds
every caller — not just the unlucky one — until the provider says it is ready.
"""

from __future__ import annotations

import asyncio
import time


class RateLimiter:
    def __init__(self, per_minute: int, window: float = 60.0) -> None:
        self.per_minute = max(0, int(per_minute))
        self.window = window
        self._grants: list[float] = []
        self._until = 0.0          # set by penalise(), shared by every caller
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return self.per_minute > 0

    def _wait_for(self, now: float) -> float:
        """Seconds this caller must wait, without taking the slot."""
        if self._until > now:
            return self._until - now
        if not self.enabled:
            return 0.0
        cutoff = now - self.window
        self._grants = [t for t in self._grants if t > cutoff]
        if len(self._grants) < self.per_minute:
            return 0.0
        return self._grants[0] + self.window - now

    async def acquire(self, on_wait=None) -> None:
        """Take one slot, sleeping until the window has room.

        The lock is held across the sleep on purpose. Callers are serialised so
        they take slots in turn; releasing early would let a dozen of them read
        the same free window and all go at once, which is the exact burst the
        limit exists to prevent.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                delay = self._wait_for(now)
                if delay <= 0:
                    self._grants.append(now)
                    return
                if on_wait:
                    on_wait(delay)
                await asyncio.sleep(min(delay, self.window))

    def penalise(self, seconds: float) -> None:
        """Hold every caller for `seconds` — the provider said it is full."""
        self._until = max(self._until, time.monotonic() + max(0.0, seconds))

    def reconfigure(self, per_minute: int) -> None:
        self.per_minute = max(0, int(per_minute))


def retry_after(headers, default: float) -> float:
    """Read a Retry-After header, in seconds. Dates are not worth parsing here.

    Providers send either a number of seconds or an HTTP date; the date form is
    rare enough, and our fallback close enough, that guessing wrong costs one
    extra wait rather than a failure.
    """
    raw = (headers.get("retry-after") or headers.get("Retry-After") or "").strip()
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return default
