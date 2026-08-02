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
from datetime import datetime, timedelta, timezone
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

-- Your own API keys, kept here rather than in the environment — because a key is
-- something you change when a limit bites, and changing an environment variable
-- means a deploy. Several per provider is the point: ten Gemini keys are ten
-- times the per-minute allowance, and the app moves to the next one the moment
-- one refuses.
--
-- `cooldown_until` is how a refusal is remembered: a key that has just been
-- rate-limited is skipped until then rather than tried again immediately and
-- refused again. `fails` and `last_error` are what the settings page shows, so a
-- key that is simply wrong can be told apart from one that is merely busy.
CREATE TABLE IF NOT EXISTS apikeys (
    id             TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    label          TEXT NOT NULL DEFAULT '',
    secret         TEXT NOT NULL,
    enabled        INTEGER NOT NULL DEFAULT 1,
    uses           INTEGER NOT NULL DEFAULT 0,
    fails          INTEGER NOT NULL DEFAULT 0,
    ok_at          TEXT NOT NULL DEFAULT '',
    failed_at      TEXT NOT NULL DEFAULT '',
    cooldown_until TEXT NOT NULL DEFAULT '',
    last_error     TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS apikeys_provider_idx ON apikeys (provider);

-- A video you have asked for in advance. `request` is the whole create payload,
-- so a plan that fires next Tuesday makes exactly the video that was described
-- today. `publish_at` is when it should be live; `lead_minutes` is how long
-- before that the app starts building, because a video takes time to make and
-- the point of planning ahead is not to be waiting at nine on Tuesday.
CREATE TABLE IF NOT EXISTS plans (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    request      TEXT NOT NULL,
    publish_at   TEXT NOT NULL,
    lead_minutes INTEGER NOT NULL DEFAULT 240,
    privacy      TEXT NOT NULL DEFAULT 'public',
    -- 1 means it waits for you to look at it before it goes anywhere. That is the
    -- default, because a video published without being read is a video you cannot
    -- unpublish from anyone who already saw it.
    approve      INTEGER NOT NULL DEFAULT 1,
    -- 'auto' takes the cheap slow road when there is time for it, 'on' always,
    -- 'off' never. A choice, because "cheaper but slower" is not a decision an
    -- app should be making silently on somebody else's behalf.
    batch        TEXT NOT NULL DEFAULT 'auto',
    status       TEXT NOT NULL DEFAULT 'planned',
    job_id       TEXT NOT NULL DEFAULT '',
    video_url    TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS plans_when_idx ON plans (publish_at);

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

-- A picture the app wants but is not going to generate itself. When the image
-- provider is `flow`, the render stops at each scene and parks its prompt here
-- instead of calling an API; something outside — a browser extension driving
-- Google Flow in a tab you are already signed into, or you with a file picker —
-- takes the prompt, makes the picture and hands it back.
--
-- The reason this is a table and not a queue in memory: a render that is waiting
-- for pictures may wait half an hour, and a deploy in the middle of that must not
-- lose the list of what it was waiting for.
CREATE TABLE IF NOT EXISTS imagetasks (
    id         TEXT PRIMARY KEY,
    job_id     TEXT NOT NULL,
    scene      INTEGER NOT NULL DEFAULT 0,
    prompt     TEXT NOT NULL,
    aspect     TEXT NOT NULL DEFAULT '16:9',
    -- Where the render is waiting for the file to appear. Absolute, because the
    -- upload arrives on an HTTP handler that has no idea which job it belongs to.
    out_path   TEXT NOT NULL,
    -- waiting → taken → done, or failed. `taken` is a soft claim: a worker that
    -- disappears mid-task has its claim expire rather than stranding the render.
    status     TEXT NOT NULL DEFAULT 'waiting',
    taken_by   TEXT NOT NULL DEFAULT '',
    taken_at   TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS imagetasks_status_idx ON imagetasks (status, created_at);
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
_ADOPTABLE = ("heroes", "music", "assets", "settings", "jobs", "media", "profiles",
              "plans", "apikeys")


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
    ("plans", "batch", "TEXT NOT NULL DEFAULT 'auto'"),
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


# --- api keys ----------------------------------------------------------------
# The secret is selected only by the keyring, never by anything that answers a
# request: `list_keys` deliberately does not return it.

_KEY_COLS = ("id, provider, label, enabled, uses, fails, ok_at, failed_at, "
             "cooldown_until, last_error, created_at")


def add_key(provider: str, secret: str, label: str = "") -> dict[str, Any]:
    key_id = new_id("key")
    with _conn() as conn:
        conn.execute(
            "INSERT INTO apikeys (id, provider, label, secret, enabled, uses, fails,"
            " ok_at, failed_at, cooldown_until, last_error, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (key_id, provider, label, secret, 1, 0, 0, "", "", "", "", _now()),
        )
    return get_key(key_id) or {}


def list_keys(provider: str = "") -> list[dict[str, Any]]:
    """Every key's state, without its secret."""
    with _conn() as conn:
        if provider:
            rows = conn.execute(
                f"SELECT {_KEY_COLS} FROM apikeys WHERE provider = ?"
                " ORDER BY created_at ASC", (provider,)).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {_KEY_COLS} FROM apikeys ORDER BY provider, created_at ASC").fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def get_key(key_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_KEY_COLS} FROM apikeys WHERE id = ?", (key_id,)).fetchone()
    return {**dict(row), "enabled": bool(row["enabled"])} if row else None


def key_secrets(provider: str) -> list[dict[str, Any]]:
    """The usable rows *with* their secrets. Only the keyring calls this."""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT id, provider, label, secret, enabled, uses, fails, cooldown_until,"
            " last_error FROM apikeys WHERE provider = ? ORDER BY created_at ASC",
            (provider,)).fetchall()
    return [{**dict(r), "enabled": bool(r["enabled"])} for r in rows]


def update_key(key_id: str, **fields: Any) -> bool:
    allowed = ("label", "enabled", "secret", "uses", "fails", "ok_at", "failed_at",
               "cooldown_until", "last_error")
    sets, values = [], []
    for name in allowed:
        if name in fields and fields[name] is not None:
            sets.append(f"{name} = ?")
            values.append(1 if fields[name] is True else 0 if fields[name] is False
                          else fields[name])
    if not sets:
        return False
    values.append(key_id)
    with _conn() as conn:
        return conn.execute(
            f"UPDATE apikeys SET {', '.join(sets)} WHERE id = ?", values).rowcount > 0


def bump_key(key_id: str, *, ok: bool, when: str, cooldown_until: str = "",
             error: str = "") -> None:
    """Record one outcome. Counters are incremented in SQL so two workers
    reporting at once cannot lose each other's count."""
    with _conn() as conn:
        if ok:
            conn.execute(
                "UPDATE apikeys SET uses = uses + 1, ok_at = ?, fails = 0,"
                " cooldown_until = '', last_error = '' WHERE id = ?", (when, key_id))
        else:
            conn.execute(
                "UPDATE apikeys SET fails = fails + 1, failed_at = ?,"
                " cooldown_until = ?, last_error = ? WHERE id = ?",
                (when, cooldown_until, error[:300], key_id))


def delete_key(key_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM apikeys WHERE id = ?", (key_id,)).rowcount > 0


# --- pictures somebody else is making ----------------------------------------

# How long a claim lasts before the task is offered again. A browser tab that is
# closed mid-generation would otherwise hold a scene for as long as the render is
# prepared to wait, which is the whole render.
CLAIM_SECONDS = 300.0

_TASK_COLS = ("id, job_id, scene, prompt, aspect, out_path, status, taken_by, "
              "taken_at, error, created_at, updated_at")


def add_image_task(*, job_id: str, scene: int, prompt: str, aspect: str,
                   out_path: str) -> dict[str, Any]:
    task_id = new_id("img")
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO imagetasks (id, job_id, scene, prompt, aspect, out_path,"
            " status, taken_by, taken_at, error, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, job_id, int(scene), prompt, aspect, out_path,
             "waiting", "", "", "", now, now))
    return get_image_task(task_id) or {}


def get_image_task(task_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_TASK_COLS} FROM imagetasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_image_tasks(*, job_id: str = "", pending_only: bool = True,
                     limit: int = 100) -> list[dict[str, Any]]:
    where, args = [], []
    if job_id:
        where.append("job_id = ?")
        args.append(job_id)
    if pending_only:
        where.append("status IN ('waiting', 'taken')")
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_TASK_COLS} FROM imagetasks{clause}"
            " ORDER BY created_at ASC LIMIT ?", (*args, int(limit))).fetchall()
    return [dict(r) for r in rows]


def claim_image_task(worker: str) -> dict[str, Any] | None:
    """Hand out the oldest unclaimed task, or one whose claim has gone stale.

    Read-then-write with the status checked again in the `WHERE`, rather than one
    clever statement: two workers racing both issue the same UPDATE, exactly one
    of them changes a row, and the loser looks for another task. That is portable
    to both databases, which `UPDATE ... LIMIT` is not.
    """
    stale = (datetime.now(timezone.utc) - timedelta(seconds=CLAIM_SECONDS)
             ).isoformat(timespec="milliseconds")
    for _ in range(8):
        with _conn() as conn:
            row = conn.execute(
                f"SELECT {_TASK_COLS} FROM imagetasks"
                " WHERE status = 'waiting' OR (status = 'taken' AND taken_at < ?)"
                " ORDER BY created_at ASC LIMIT 1", (stale,)).fetchone()
            if row is None:
                return None
            changed = conn.execute(
                "UPDATE imagetasks SET status='taken', taken_by=?, taken_at=?, updated_at=?"
                " WHERE id=? AND status=?",
                (worker, _now(), _now(), row["id"], row["status"])).rowcount
        if changed:
            return get_image_task(row["id"])
    return None


def finish_image_task(task_id: str, *, error: str = "") -> bool:
    with _conn() as conn:
        return conn.execute(
            "UPDATE imagetasks SET status=?, error=?, updated_at=? WHERE id=?",
            ("failed" if error else "done", error[:300], _now(), task_id)).rowcount > 0


def release_image_task(task_id: str) -> bool:
    """Put a claimed task back in the queue, unfinished."""
    with _conn() as conn:
        return conn.execute(
            "UPDATE imagetasks SET status='waiting', taken_by='', taken_at='',"
            " updated_at=? WHERE id=? AND status='taken'",
            (_now(), task_id)).rowcount > 0


def rewrite_image_task(task_id: str, prompt: str) -> bool:
    """Change what a waiting picture is a picture of.

    A claim is dropped at the same time, deliberately: a worker holding the old
    wording would go and draw it, and the edit would have changed nothing except
    what the screen said. Handing it back means the next worker picks up the new
    prompt — which is what editing it was for.
    """
    with _conn() as conn:
        return conn.execute(
            "UPDATE imagetasks SET prompt=?, status='waiting', taken_by='',"
            " taken_at='', updated_at=? WHERE id=? AND status IN ('waiting','taken')",
            (prompt, _now(), task_id)).rowcount > 0


def drop_image_tasks(job_id: str) -> int:
    """Forget what a job was waiting for — it is no longer waiting."""
    with _conn() as conn:
        return conn.execute(
            "DELETE FROM imagetasks WHERE job_id = ? AND status IN ('waiting','taken')",
            (job_id,)).rowcount


# --- plans -------------------------------------------------------------------

_PLAN_COLS = ("id, title, request, publish_at, lead_minutes, privacy, approve, "
              "batch, status, job_id, video_url, error, created_at, updated_at")


def _plan(row: Any) -> dict[str, Any]:
    out = dict(row)
    try:
        out["request"] = json.loads(out.get("request") or "{}")
    except json.JSONDecodeError:
        out["request"] = {}
    out["approve"] = bool(out.get("approve"))
    return out


def add_plan(*, title: str, request: dict[str, Any], publish_at: str,
             lead_minutes: int = 240, privacy: str = "public",
             approve: bool = True, batch: str = "auto") -> dict[str, Any]:
    plan_id = new_id("pln")
    now = _now()
    with _conn() as conn:
        conn.execute(
            "INSERT INTO plans (id, title, request, publish_at, lead_minutes, privacy,"
            " approve, batch, status, job_id, video_url, error, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (plan_id, title, json.dumps(request), publish_at, int(lead_minutes),
             privacy, 1 if approve else 0, batch, "planned", "", "", "", now, now),
        )
    return get_plan(plan_id) or {}


def list_plans(limit: int = 60) -> list[dict[str, Any]]:
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_PLAN_COLS} FROM plans ORDER BY publish_at ASC LIMIT ?",
            (limit,)).fetchall()
    return [_plan(r) for r in rows]


def get_plan(plan_id: str) -> dict[str, Any] | None:
    with _conn() as conn:
        row = conn.execute(
            f"SELECT {_PLAN_COLS} FROM plans WHERE id = ?", (plan_id,)).fetchone()
    return _plan(row) if row else None


def update_plan(plan_id: str, **fields: Any) -> bool:
    """Change a plan. Only the columns named are touched."""
    allowed = ("title", "publish_at", "lead_minutes", "privacy", "approve",
               "batch", "status", "job_id", "video_url", "error")
    sets, values = [], []
    for key in allowed:
        if key in fields and fields[key] is not None:
            sets.append(f"{key} = ?")
            values.append(1 if key == "approve" and fields[key] is True
                          else 0 if key == "approve" and fields[key] is False
                          else fields[key])
    if "request" in fields and fields["request"] is not None:
        sets.append("request = ?")
        values.append(json.dumps(fields["request"]))
    if not sets:
        return False
    sets.append("updated_at = ?")
    values.append(_now())
    values.append(plan_id)
    with _conn() as conn:
        return conn.execute(
            f"UPDATE plans SET {', '.join(sets)} WHERE id = ?", values).rowcount > 0


def delete_plan(plan_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,)).rowcount > 0


def plans_due(now_iso: str, statuses: tuple[str, ...] = ("planned",)) -> list[dict[str, Any]]:
    """Plans whose build should already have started.

    The comparison is done in Python rather than SQL: `publish_at` minus a
    per-plan lead time is not something either engine can index on, and there are
    tens of these rows, not millions.
    """
    from datetime import datetime, timedelta, timezone as _tz

    def parsed(text: str) -> datetime | None:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    right_now = parsed(now_iso) or datetime.now(_tz.utc)
    out = []
    for plan in list_plans(200):
        if plan["status"] not in statuses:
            continue
        when = parsed(plan["publish_at"])
        if when is None:
            continue
        if when - timedelta(minutes=int(plan["lead_minutes"] or 0)) <= right_now:
            out.append(plan)
    return out


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
