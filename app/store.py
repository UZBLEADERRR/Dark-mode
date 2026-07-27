"""SQLite persistence for heroes, music and render jobs.

Deliberately synchronous and tiny: every call opens a short-lived connection,
which keeps it safe to touch from both the request handlers and the background
worker without sharing connection state across threads.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heroes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS music (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    step         TEXT NOT NULL DEFAULT '',
    progress     INTEGER NOT NULL DEFAULT 0,
    request      TEXT NOT NULL,
    result       TEXT NOT NULL DEFAULT '{}',
    logs         TEXT NOT NULL DEFAULT '[]',
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    config.ensure_dirs()
    with _conn() as conn:
        conn.executescript(_SCHEMA)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- heroes ------------------------------------------------------------------

def add_hero(name: str, description: str, filename: str) -> dict[str, Any]:
    hero_id = new_id("hero")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO heroes (id, name, description, filename, created_at) VALUES (?,?,?,?,?)",
            (hero_id, name, description, filename, _now()),
        )
    return {"id": hero_id, "name": name, "description": description, "filename": filename}


def list_heroes() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM heroes ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_heroes(hero_ids: list[str]) -> list[dict[str, Any]]:
    if not hero_ids:
        return []
    placeholders = ",".join("?" for _ in hero_ids)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM heroes WHERE id IN ({placeholders})", hero_ids
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[h] for h in hero_ids if h in by_id]


def delete_hero(hero_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM heroes WHERE id = ?", (hero_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM heroes WHERE id = ?", (hero_id,))
    return dict(row)


# --- music -------------------------------------------------------------------

def add_music(name: str, filename: str) -> dict[str, Any]:
    music_id = new_id("mus")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO music (id, name, filename, created_at) VALUES (?,?,?,?)",
            (music_id, name, filename, _now()),
        )
    return {"id": music_id, "name": name, "filename": filename}


def list_music() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM music ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_music(music_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM music WHERE id = ?", (music_id,)).fetchone()
    return dict(row) if row else None


def delete_music(music_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM music WHERE id = ?", (music_id,)).fetchone()
        if row is None:
            return None
        conn.execute("DELETE FROM music WHERE id = ?", (music_id,))
    return dict(row)


# --- jobs --------------------------------------------------------------------

def create_job(request: dict[str, Any]) -> str:
    job_id = new_id("job")
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO jobs (id, status, step, progress, request, result, logs, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, "queued", "queued", 0, json.dumps(request), "{}", "[]", now, now),
        )
    return job_id


def update_job(
    job_id: str,
    *,
    status: str | None = None,
    step: str | None = None,
    progress: int | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    log: str | None = None,
) -> None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return
        merged_result = json.loads(row["result"] or "{}")
        if result:
            merged_result.update(result)
        logs = json.loads(row["logs"] or "[]")
        if log:
            logs.append(f"[{time.strftime('%H:%M:%S')}] {log}")
            logs = logs[-200:]
        conn.execute(
            "UPDATE jobs SET status=?, step=?, progress=?, result=?, logs=?, error=?, updated_at=?"
            " WHERE id=?",
            (
                status or row["status"],
                step if step is not None else row["step"],
                progress if progress is not None else row["progress"],
                json.dumps(merged_result),
                json.dumps(logs),
                error if error is not None else row["error"],
                _now(),
                job_id,
            ),
        )


def get_job(job_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["request"] = json.loads(data["request"] or "{}")
    data["result"] = json.loads(data["result"] or "{}")
    data["logs"] = json.loads(data["logs"] or "[]")
    return data


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["request"] = json.loads(data["request"] or "{}")
        data["result"] = json.loads(data["result"] or "{}")
        data["logs"] = json.loads(data["logs"] or "[]")
        out.append(data)
    return out


def delete_job(job_id: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0


def reset_stale_jobs() -> None:
    """Any job left 'running' belongs to a container that no longer exists."""
    with _conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error=? , updated_at=?"
            " WHERE status IN ('running','queued')",
            ("Server restarted while this job was running.", _now()),
        )
