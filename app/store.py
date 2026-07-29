"""Persistence for heroes, music, assets, settings and render jobs.

Deliberately synchronous and tiny: every call opens a short-lived connection,
which keeps it safe to touch from both the request handlers and the background
worker without sharing connection state across threads.

SQLite by default, beside the render output. Set `DATABASE_URL` and *everything*
moves to Postgres instead — because a container filesystem does not survive a
deploy, and losing a hero photo, an uploaded sound, or the half-finished project
you were going to come back to is not something the user can undo.

There is one set of SQL, not two. `pgstore` translates the handful of things the
two engines actually spell differently, so a query cannot work locally and then
quietly misbehave in production: it is the same statement in both places.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from . import config, pgstore

def _schema_for(blob: str) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS heroes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mime        TEXT NOT NULL DEFAULT 'image/png',
    ext         TEXT NOT NULL DEFAULT '.png',
    image       {blob} NOT NULL,
    created_at  TEXT NOT NULL
);

-- `kind` separates a background bed ('music') from a one-shot sting ('sfx');
-- they are the same kind of file and differ only in how the renderer uses them.
CREATE TABLE IF NOT EXISTS music (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'music',
    mime        TEXT NOT NULL DEFAULT 'audio/mpeg',
    ext         TEXT NOT NULL DEFAULT '.mp3',
    audio       {blob} NOT NULL,
    created_at  TEXT NOT NULL
);

-- One row per named blob of JSON. Right now that is the brand kit; anything
-- else app-wide can live here without another table.
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Stickers, logos, cut-out actors and recorded voice takes: anything dropped
-- onto a scene as a layer.
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    mime        TEXT NOT NULL DEFAULT 'image/png',
    ext         TEXT NOT NULL DEFAULT '.png',
    data        {blob} NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL,
    step         TEXT NOT NULL DEFAULT '',
    progress     INTEGER NOT NULL DEFAULT 0,
    request      TEXT NOT NULL,
    result       TEXT NOT NULL DEFAULT '{{}}',
    logs         TEXT NOT NULL DEFAULT '[]',
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_created_idx ON jobs (created_at DESC);
"""


def _schema() -> str:
    return _schema_for(pgstore.blob())


def _now() -> str:
    # Milliseconds, not seconds: several heroes uploaded in the same second
    # would otherwise share a timestamp, and "newest first" became whatever
    # order the database felt like. Rows written at the old precision still
    # sort correctly against these — '+' sorts before '.', so a bare second
    # reads as .000.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@contextmanager
def _sqlite() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _prepare(conn: Any) -> None:
    """Declare the schema once per process, on the first live connection."""
    if pgstore.mark_ready():
        conn.executescript(_schema())
        _migrate_pg(conn)


@contextmanager
def _conn() -> Iterator[Any]:
    """Whichever database is configured, behind one interface.

    The schema runs on the first Postgres connection of the process rather than
    at startup: the app has to answer its healthcheck even when the database is
    having a bad minute, and a table that already exists costs nothing to
    re-declare.
    """
    if not pgstore.enabled():
        with _sqlite() as conn:
            yield conn
        return
    with pgstore.connect() as conn:
        _prepare(conn)
        yield conn
        conn.commit()


def init() -> None:
    """Prepare storage. Never raises — the container has to boot regardless.

    A database that is having a bad minute must not stop the app from starting:
    a failed healthcheck rolls the deploy back, and then the outage that would
    have lasted a minute lasts until someone notices. The local file is always
    prepared; Postgres prepares itself on its first successful connection.
    """
    config.ensure_dirs()
    with _sqlite() as conn:
        conn.executescript(_schema_for(blob="BLOB"))
        _migrate_sqlite(conn)
    if pgstore.enabled():
        try:
            with _conn() as conn:
                conn.execute("SELECT 1").fetchone()
        except Exception:  # noqa: BLE001 - reported on the settings page instead
            return
        _adopt_local_rows()


# Everything that may already be sitting in a local SQLite file.
_ADOPTABLE = ("heroes", "music", "assets", "settings", "jobs")


def _adopt_local_rows() -> None:
    """Move what SQLite already holds into Postgres the first time it is set up.

    Someone who has been using the app against a container and then attaches a
    database should find their library intact, not empty. Inserts skip rows that
    are already there, so this is safe on every boot; a database that is
    unreachable right now leaves the local copies exactly where they were.

    Projects are copied too. They are the expensive thing — a project carries the
    script, the voice-over timings and the scene list that were paid for once —
    and losing those on the deploy that fixed persistence would be a poor joke.
    """
    if not pgstore.enabled():
        return
    try:
        for table in _ADOPTABLE:
            _adopt_table(table)
    except Exception:  # noqa: BLE001 - a migration is not worth failing startup for
        pass


def _adopt_table(table: str) -> None:
    key = "key" if table == "settings" else "id"
    try:
        with _sqlite() as local:
            rows = [dict(r) for r in local.execute(f"SELECT * FROM {table}").fetchall()]
    except sqlite3.Error:
        return  # no local database yet, which is the common case
    if not rows:
        return

    with pgstore.connect() as conn:
        _prepare(conn)
        existing = {r[key] for r in conn.execute(f"SELECT {key} FROM {table}").fetchall()}
        for row in rows:
            if row[key] in existing:
                continue
            columns = list(row)
            marks = ",".join("?" for _ in columns)
            conn.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({marks})",
                [row[c] for c in columns],
            )
        conn.commit()


# Columns that arrived after a database was first created. CREATE TABLE IF NOT
# EXISTS is a no-op on an existing table, so a new column in the schema above
# never reaches a database that already has the table.

def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(music)")}
    if "kind" not in columns:
        conn.execute("ALTER TABLE music ADD COLUMN kind TEXT NOT NULL DEFAULT 'music'")


def _migrate_pg(conn: Any) -> None:
    conn.execute(
        "ALTER TABLE music ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'music'")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- heroes ------------------------------------------------------------------
# Metadata queries deliberately never SELECT * — the blob column would then ride
# along on every list call.

_HERO_COLS = "id, name, description, mime, ext, created_at"


def add_hero(name: str, description: str, image: bytes, mime: str, ext: str) -> dict[str, Any]:
    hero_id = new_id("hero")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO heroes (id, name, description, mime, ext, image, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (hero_id, name, description, mime, ext, image, _now()),
        )
    return {"id": hero_id, "name": name, "description": description, "mime": mime, "ext": ext}


def list_heroes() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_HERO_COLS} FROM heroes ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_heroes(hero_ids: list[str]) -> list[dict[str, Any]]:
    if not hero_ids:
        return []
    placeholders = ",".join("?" for _ in hero_ids)
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_HERO_COLS} FROM heroes WHERE id IN ({placeholders})", hero_ids
        ).fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    return [by_id[h] for h in hero_ids if h in by_id]


def get_hero_image(hero_id: str) -> tuple[bytes, str, str] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT image, mime, ext FROM heroes WHERE id = ?", (hero_id,)
        ).fetchone()
    # Postgres hands bytes back as a memoryview; everything downstream wants
    # real bytes, and bytes() on bytes is free.
    return (bytes(row["image"]), row["mime"], row["ext"]) if row else None


def update_hero(hero_id: str, *, name: str | None = None, description: str | None = None) -> bool:
    fields, values = [], []
    if name is not None:
        fields.append("name = ?")
        values.append(name)
    if description is not None:
        fields.append("description = ?")
        values.append(description)
    if not fields:
        return False
    values.append(hero_id)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE heroes SET {', '.join(fields)} WHERE id = ?", values)
        return cur.rowcount > 0


def delete_hero(hero_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM heroes WHERE id = ?", (hero_id,)).rowcount > 0


# --- music -------------------------------------------------------------------

_MUSIC_COLS = "id, name, kind, mime, ext, created_at"


def add_music(name: str, audio: bytes, mime: str, ext: str,
              kind: str = "music") -> dict[str, Any]:
    music_id = new_id("sfx" if kind == "sfx" else "mus")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO music (id, name, kind, mime, ext, audio, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (music_id, name, kind, mime, ext, audio, _now()),
        )
    return {"id": music_id, "name": name, "kind": kind, "mime": mime, "ext": ext}


def list_music(kind: str | None = None) -> list[dict[str, Any]]:
    query = f"SELECT {_MUSIC_COLS} FROM music"
    params: tuple = ()
    if kind:
        query += " WHERE kind = ?"
        params = (kind,)
    with _conn() as conn:
        rows = conn.execute(query + " ORDER BY created_at DESC", params).fetchall()
    return [dict(r) for r in rows]


def get_music_audio(music_id: str) -> tuple[bytes, str, str] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT audio, mime, ext FROM music WHERE id = ?", (music_id,)
        ).fetchone()
    return (bytes(row["audio"]), row["mime"], row["ext"]) if row else None


def delete_music(music_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM music WHERE id = ?", (music_id,)).rowcount > 0


# --- settings (the brand kit) ------------------------------------------------

def get_setting(key: str, default: Any = None) -> Any:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default


def set_setting(key: str, value: Any) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, json.dumps(value), _now()),
        )


# --- overlay assets ----------------------------------------------------------

_ASSET_COLS = "id, name, mime, ext, created_at"


def add_asset(name: str, data: bytes, mime: str, ext: str) -> dict[str, Any]:
    asset_id = new_id("ast")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO assets (id, name, mime, ext, data, created_at) VALUES (?,?,?,?,?,?)",
            (asset_id, name, mime, ext, data, _now()),
        )
    return {"id": asset_id, "name": name, "mime": mime, "ext": ext}


def list_assets() -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_ASSET_COLS} FROM assets ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_asset(asset_id: str) -> tuple[bytes, str, str] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT data, mime, ext FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
    return (bytes(row["data"]), row["mime"], row["ext"]) if row else None


def delete_asset(asset_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,)).rowcount > 0


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


def replace_request(job_id: str, request: dict[str, Any]) -> bool:
    """Rewrite a job's settings in place — used when the editor changes the look."""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET request = ?, updated_at = ? WHERE id = ?",
            (json.dumps(request), _now(), job_id),
        )
        return cur.rowcount > 0


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


# How many times a job may be picked back up after the container died under it.
# Without a ceiling a render that reliably exhausts the container's memory would
# restart it, be resumed, and exhaust it again — for ever.
MAX_RESUMES = 2


def recover_jobs() -> list[str]:
    """Settle jobs the last container was in the middle of, and say what can go on.

    A restart used to fail every job in flight, which threw away work that was
    already on disk: scenes are written between stages, so a video interrupted
    during the render still has its script, its voice-over and its pictures.

    Anything with saved scenes goes back to `review`, where it can be rendered
    again without paying for any of that twice. The ids returned are the ones
    that were mid-render and are worth picking up automatically. A job with
    nothing saved has nothing to resume, so it fails as it always did.
    """
    resumable: list[str] = []
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, status, result FROM jobs WHERE status IN ('running','queued','rendering')"
        ).fetchall()

        for row in rows:
            result = json.loads(row["result"] or "{}")
            scenes = result.get("scenes") or []
            logs_note = "Server restarted — "
            resumes = int(result.get("resumes", 0))

            if not scenes:
                status, error = "failed", "The server restarted before anything was saved."
                logs_note += "nothing had been saved yet."
            elif row["status"] == "rendering" and resumes < MAX_RESUMES:
                status, error = "review", ""
                result["resumes"] = resumes + 1
                logs_note += "picking the render back up where it left off."
                resumable.append(row["id"])
            else:
                status, error = "review", ""
                logs_note += (
                    "your scenes are safe — press Render to finish."
                    if resumes < MAX_RESUMES else
                    "this job has been interrupted repeatedly; render it by hand."
                )

            logs = json.loads(row["logs"] or "[]") if "logs" in row.keys() else []
            conn.execute(
                "UPDATE jobs SET status=?, step=?, progress=?, result=?, error=?, updated_at=?"
                " WHERE id=?",
                (status, status, 72 if scenes else 0, json.dumps(result), error, _now(), row["id"]),
            )
            _append_log(conn, row["id"], logs_note)
    return resumable


def _append_log(conn: Any, job_id: str, message: str) -> None:
    row = conn.execute("SELECT logs FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        return
    logs = json.loads(row["logs"] or "[]")
    logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
    conn.execute("UPDATE jobs SET logs=? WHERE id=?", (json.dumps(logs[-200:]), job_id))


# The old name, kept so nothing that calls it breaks.
reset_stale_jobs = recover_jobs
