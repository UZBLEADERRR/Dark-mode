"""Videos asked for in advance, and the loop that makes them happen.

The shape of a plan is: *this video, live at this time.* Between those two facts
sit a draft, a render, and — by default — a person looking at it. So the loop has
one job per state and never more than one:

    planned  ──(lead time reached)──▶ building ──(render done)──▶ ready
                                          │                        │
                                          └──(failed)──▶ failed    │
                                                                   ▼
                                  approved by you ──▶ published (YouTube holds it
                                                      until publish_at)

Two decisions are worth stating outright.

**It waits for you.** A plan is approve-first by default, because a video that
went up unread cannot be un-seen by whoever already saw it. Approval is not a
race against the clock, though: YouTube's own `publishAt` does the scheduling, so
approving at any point before the slot still publishes exactly on time.

**It starts early.** `lead_minutes` before the slot, not at it. A fifty-scene
video takes real time to draw and render, and the whole point of planning ahead
is not to be waiting at nine on Tuesday morning.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import config, pipeline, store
from .models import CreateJobRequest
from .providers import youtube
from .render import subtitles as subs

# How often the loop looks. A minute is far more often than a plan needs and
# cheap enough not to matter: it is one query against tens of rows.
TICK_SECONDS = 60

# Any plan whose slot is at least this far away is built with the patient path —
# the batch API, which is half the price and answers within a day rather than
# within a minute.
BATCH_AFTER_HOURS = 6

BUILDING = {"queued", "running", "rendering"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def hours_until(when: str) -> float:
    moment = _parse(when)
    if moment is None:
        return 0.0
    return (moment - datetime.now(timezone.utc)).total_seconds() / 3600.0


def wants_batch(plan: dict[str, Any]) -> bool:
    """Whether there is enough time to take the cheap, slow road."""
    return hours_until(plan.get("publish_at", "")) >= BATCH_AFTER_HOURS


def describe(plan: dict[str, Any]) -> str:
    """One line for the card. What is happening, and what happens next."""
    status = plan.get("status")
    when = plan.get("publish_at", "")
    if status == "planned":
        lead = int(plan.get("lead_minutes") or 0)
        moment = _parse(when)
        start = (moment - timedelta(minutes=lead)) if moment else None
        left = hours_until(when)
        if start and start > datetime.now(timezone.utc):
            return (f"Rejada. Tayyorlash {start.strftime('%d.%m %H:%M')} da boshlanadi"
                    f" — chiqishiga {left:.0f} soat qoldi." if left >= 1
                    else f"Rejada. Tayyorlash {start.strftime('%d.%m %H:%M')} da boshlanadi.")
        return "Navbatda — tayyorlash boshlanadi."
    if status == "building":
        return "Tayyorlanmoqda."
    if status == "ready":
        return "Tayyor — ko'rib chiqib tasdiqlang, keyin belgilangan vaqtda chiqadi."
    if status == "published":
        return "YouTube'ga joylandi — belgilangan vaqtda chiqadi."
    if status == "failed":
        return f"Xato: {plan.get('error') or 'sabab yozilmagan'}"
    if status == "cancelled":
        return "Bekor qilingan."
    return status or ""


# --- the states ---------------------------------------------------------------

async def start_plan(plan: dict[str, Any], launch: Callable[..., None]) -> None:
    """Turn a plan into a project and set it building."""
    payload = dict(plan.get("request") or {})
    # A planned video is made all the way through: the point is that it is ready
    # to look at when you get to it, not that it is waiting to be started.
    payload["auto_render"] = True
    payload.setdefault("caption_style", subs.resolve_style(payload.get("subtitle_style", "bold")))
    if wants_batch(plan):
        # Cheap and slow, because there is time. How much time is the plan's own
        # answer: half of whatever is left before the slot, and never more than a
        # few hours — a batch still waiting when the video is due has stopped
        # being a saving and started being a missed slot.
        payload["batch"] = True
        left = max(0.0, hours_until(plan.get("publish_at", "")))
        # `setdefault`, so a plan that carries its own budget keeps it — the slot
        # is the usual answer, not the only possible one.
        payload.setdefault("batch_patience_minutes", round(min(180.0, left * 30), 1))

    try:
        request = CreateJobRequest(**payload)
    except Exception as exc:  # noqa: BLE001 - a plan with a bad payload is a dead plan
        store.update_plan(plan["id"], status="failed",
                          error=f"Reja so'rovi noto'g'ri: {exc}")
        return

    job_id = store.create_job(request.model_dump())
    store.update_plan(plan["id"], status="building", job_id=job_id, error="")
    store.update_job(job_id, log=f"Rejadan boshlandi — {plan['publish_at']} da chiqishi kerak")
    launch(lambda: pipeline.run_draft(job_id), job_id)


async def follow_plan(plan: dict[str, Any]) -> None:
    """Watch a building plan and move it on when its project settles."""
    job = store.get_job(plan.get("job_id") or "")
    if job is None:
        store.update_plan(plan["id"], status="failed",
                          error="Loyiha topilmadi — o'chirilgan bo'lsa kerak.")
        return
    if job["status"] in BUILDING:
        return
    if job["status"] == "failed":
        store.update_plan(plan["id"], status="failed",
                          error=job.get("error") or "Video tayyorlanmadi.")
        return
    if job["status"] != "done":
        # A draft that stopped for review is not a finished video. Nothing here can
        # push it on, so it is reported rather than left looking busy for ever.
        store.update_plan(
            plan["id"], status="failed",
            error="Video render bo'lmadi — loyihani ochib «Render» bosing, "
                  "keyin rejani tasdiqlang.")
        return

    if plan.get("approve"):
        store.update_plan(plan["id"], status="ready")
        return
    await publish_plan(plan["id"])


async def publish_plan(plan_id: str) -> dict[str, Any]:
    """Put a plan's finished video up, timed to its slot.

    Raises `PlanError` with something worth reading. Called both by the loop, for
    a plan that runs itself, and by the approve button.
    """
    plan = store.get_plan(plan_id)
    if plan is None:
        raise PlanError("Reja topilmadi.")
    job = store.get_job(plan.get("job_id") or "")
    if job is None or job["status"] != "done":
        raise PlanError("Bu rejaning videosi hali tayyor emas.")
    if not youtube.connected():
        raise PlanError("YouTube kanali ulanmagan — Kutubxonada ulang.")

    local = await pipeline.finished_file(plan["job_id"])
    if local is None:
        raise PlanError("Video fayli topilmadi — loyihani qayta render qiling.")

    result = job.get("result") or {}
    meta = (result.get("metadata") or {}).get("youtube") or {}
    # The slot itself. A moment already past is published now rather than being
    # refused: YouTube rejects a publishAt in the past, and "you missed it, start
    # again" is a worse answer than "here it is".
    slot = plan["publish_at"] if hours_until(plan["publish_at"]) > 0.05 else None

    try:
        made = await youtube.upload(
            local,
            title=(meta.get("title") or plan.get("title") or result.get("title") or "Video"),
            description=meta.get("description") or "",
            tags=meta.get("tags") or [],
            privacy=plan.get("privacy") or "public",
            publish_at=slot,
            language=job["request"].get("language", ""),
        )
    except youtube.YouTubeError as exc:
        store.update_plan(plan_id, status="ready", error=str(exc))
        raise PlanError(str(exc)) from exc

    shot = await pipeline.thumbnail_file(plan["job_id"])
    if shot is not None:
        await youtube.set_thumbnail(made["id"], shot)

    store.update_job(plan["job_id"], result={**result, "youtube": made},
                     log=f"Reja bo'yicha YouTube'ga joylandi: {made['url']}")
    store.update_plan(plan_id, status="published", video_url=made["url"], error="")
    return made


class PlanError(RuntimeError):
    pass


# --- the loop -----------------------------------------------------------------

async def tick(launch: Callable[..., None]) -> dict[str, int]:
    """One pass. Returns what it did, which is what the tests read."""
    did = {"started": 0, "followed": 0, "published": 0, "failed": 0}

    for plan in store.plans_due(now_iso(), ("planned",)):
        # One at a time through the ordinary queue, so a morning with five plans
        # in it does not try to render five videos at once.
        await start_plan(plan, launch)
        did["started"] += 1

    for plan in store.list_plans(200):
        if plan["status"] != "building":
            continue
        before = plan["status"]
        await follow_plan(plan)
        after = (store.get_plan(plan["id"]) or {}).get("status", before)
        if after != before:
            did["followed"] += 1
            if after == "published":
                did["published"] += 1
            elif after == "failed":
                did["failed"] += 1
    return did


async def run_forever(launch: Callable[..., None]) -> None:
    """The background loop. Nothing in here may take the app down.

    A plan is a promise about the future, and the future includes the database
    being briefly unreachable and a provider being briefly unhappy. Neither is a
    reason to stop keeping the other promises.
    """
    while True:
        try:
            await tick(launch)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            print(f"[sarideo] Reja tekshirilmadi: {exc}", flush=True)
        await asyncio.sleep(TICK_SECONDS)
