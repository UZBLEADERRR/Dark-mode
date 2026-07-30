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
-- A hero is a character: a photo, a description, and — once it starts
-- speaking for itself — a voice of its own.
CREATE TABLE IF NOT EXISTS heroes (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    mime         TEXT NOT NULL DEFAULT 'image/png',
    ext          TEXT NOT NULL DEFAULT '.png',
    image        {blob} NOT NULL,
    voice_id     TEXT NOT NULL DEFAULT '',
    tts_provider TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
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

-- Everything a render made on the way to a video: scene pictures, voice clips.
-- Object storage is the better home for these and is used when it is
-- configured; this is what keeps the promise when it is not. `path` is the
-- file's place under the projects directory, so a restored container puts it
-- back exactly where the scene list expects it.
CREATE TABLE IF NOT EXISTS media (
    path       TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    data       {blob} NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS media_job_idx ON media (job_id);

-- A screenshot of one of your own channels. `summary` is what the model saw in it —
-- the handle, the niche, who watches, how the posts are written — worked out
-- once, when the picture was uploaded, and kept. Every later conversation about
-- ideas reads that text instead of the picture, so looking at a channel is paid
-- for once rather than on every question.
CREATE TABLE IF NOT EXISTS profiles (
    id         TEXT PRIMARY KEY,
    platform   TEXT NOT NULL,
    handle     TEXT NOT NULL DEFAULT '',
    summary    TEXT NOT NULL DEFAULT '',
    -- The specifics, kept apart from the prose. A summary is what a person reads
    -- back; these are what the assistant is actually held to when it proposes
    -- something, and an idea that ignores them is the generic idea this exists
    -- to prevent.
    niche      TEXT NOT NULL DEFAULT '',
    audience   TEXT NOT NULL DEFAULT '',
    language   TEXT NOT NULL DEFAULT '',
    pillars    TEXT NOT NULL DEFAULT '',
    style      TEXT NOT NULL DEFAULT '',
    mime       TEXT NOT NULL DEFAULT 'image/png',
    ext        TEXT NOT NULL DEFAULT '.png',
    image      {blob} NOT NULL,
    created_at TEXT NOT NULL
);
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
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            # Said once, at the top of the log, in the words that name the fix.
            # The stack trace that used to appear here said "Network is
            # unreachable" and nothing about which setting was wrong.
            print(f"[sarideo] Bazaga ulanmadi: {pgstore.explain(exc)}", flush=True)
            return
        _adopt_local_rows()


# Everything that may already be sitting in a local SQLite file.
_ADOPTABLE = ("heroes", "music", "assets", "settings", "jobs", "media", "profiles")


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
    key = {"settings": "key", "media": "path"}.get(table, "id")
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

_ADDED_COLUMNS = (
    ("music", "kind", "TEXT NOT NULL DEFAULT 'music'"),
    ("heroes", "voice_id", "TEXT NOT NULL DEFAULT ''"),
    ("heroes", "tts_provider", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "niche", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "audience", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "language", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "pillars", "TEXT NOT NULL DEFAULT ''"),
    ("profiles", "style", "TEXT NOT NULL DEFAULT ''"),
)


def _migrate_sqlite(conn: sqlite3.Connection) -> None:
    for table, column, spec in _ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")


def _migrate_pg(conn: Any) -> None:
    for table, column, spec in _ADDED_COLUMNS:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {spec}")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- heroes ------------------------------------------------------------------
# Metadata queries deliberately never SELECT * — the blob column would then ride
# along on every list call.

_HERO_COLS = "id, name, description, mime, ext, voice_id, tts_provider, created_at"


def add_hero(name: str, description: str, image: bytes, mime: str, ext: str,
             voice_id: str = "", tts_provider: str = "") -> dict[str, Any]:
    hero_id = new_id("hero")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO heroes (id, name, description, mime, ext, image, voice_id,"
            " tts_provider, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (hero_id, name, description, mime, ext, image, voice_id, tts_provider, _now()),
        )
    return {"id": hero_id, "name": name, "description": description, "mime": mime,
            "ext": ext, "voice_id": voice_id, "tts_provider": tts_provider}


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


def update_hero(hero_id: str, *, name: str | None = None, description: str | None = None,
                voice_id: str | None = None, tts_provider: str | None = None) -> bool:
    fields, values = [], []
    for column, value in (("name", name), ("description", description),
                          ("voice_id", voice_id), ("tts_provider", tts_provider)):
        if value is not None:
            fields.append(f"{column} = ?")
            values.append(value)
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


# --- channel profiles --------------------------------------------------------

_PROFILE_FIELDS = ("niche", "audience", "language", "pillars", "style")
_PROFILE_COLS = ("id, platform, handle, summary, niche, audience, language, "
                 "pillars, style, mime, ext, created_at")


def add_profile(platform: str, handle: str, summary: str, data: bytes,
                mime: str, ext: str, **read: str) -> dict[str, Any]:
    """Keep a channel screenshot and everything that was read off it."""
    profile_id = new_id("prf")
    extra = {field: str(read.get(field) or "") for field in _PROFILE_FIELDS}
    with _conn() as conn:
        conn.execute(
            "INSERT INTO profiles (id, platform, handle, summary, niche, audience,"
            " language, pillars, style, mime, ext, image, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (profile_id, platform, handle, summary, extra["niche"], extra["audience"],
             extra["language"], extra["pillars"], extra["style"], mime, ext, data, _now()),
        )
    return {"id": profile_id, "platform": platform, "handle": handle,
            "summary": summary, "mime": mime, "ext": ext, **extra}


def list_profiles(platform: str = "") -> list[dict[str, Any]]:
    with _conn() as conn:
        if platform:
            rows = conn.execute(
                f"SELECT {_PROFILE_COLS} FROM profiles WHERE platform = ? "
                "ORDER BY created_at DESC", (platform,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_PROFILE_COLS} FROM profiles ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_profile_image(profile_id: str) -> tuple[bytes, str, str] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT image, mime, ext FROM profiles WHERE id = ?", (profile_id,)).fetchone()
    return (bytes(row["image"]), row["mime"], row["ext"]) if row else None


def update_profile(profile_id: str, *, handle: str | None = None,
                   summary: str | None = None, **read: str | None) -> bool:
    sets, values = [], []
    if handle is not None:
        sets.append("handle = ?")
        values.append(handle)
    if summary is not None:
        sets.append("summary = ?")
        values.append(summary)
    for field in _PROFILE_FIELDS:
        if read.get(field) is not None:
            sets.append(f"{field} = ?")
            values.append(read[field])
    if not sets:
        return False
    values.append(profile_id)
    with _conn() as conn:
        return conn.execute(
            f"UPDATE profiles SET {', '.join(sets)} WHERE id = ?", values).rowcount > 0


def delete_profile(profile_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,)).rowcount > 0


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
        # The media goes with it. Nothing else refers to a deleted project's
        # pictures, and they are the largest thing here by a wide margin.
        conn.execute("DELETE FROM media WHERE job_id = ?", (job_id,))
        cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0


# --- render media ------------------------------------------------------------
# Kept only when there is nowhere better. A file this size belongs in object
# storage; it is here so that "everything is saved" is true with a database
# alone, which is how most people will have set this up.

def put_media(job_id: str, path: str, data: bytes) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO media (path, job_id, data, created_at) VALUES (?,?,?,?)"
            " ON CONFLICT(path) DO UPDATE SET data = excluded.data,"
            " created_at = excluded.created_at",
            (path, job_id, data, _now()),
        )


def get_media(path: str) -> bytes | None:
    with _conn() as conn:
        row = conn.execute("SELECT data FROM media WHERE path = ?", (path,)).fetchone()
    return bytes(row["data"]) if row else None


def stored_media(job_id: str) -> set[str]:
    """Which of a project's files are already kept, so none is written twice."""
    with _conn() as conn:
        return {r["path"] for r in
                conn.execute("SELECT path FROM media WHERE job_id = ?", (job_id,)).fetchall()}


def media_bytes(job_id: str | None = None) -> int:
    query = "SELECT COALESCE(SUM(LENGTH(data)), 0) AS total FROM media"
    params: tuple = ()
    if job_id:
        query += " WHERE job_id = ?"
        params = (job_id,)
    with _conn() as conn:
        return int(conn.execute(query, params).fetchone()["total"] or 0)


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
