"""Postgres storage for the hero library.

Railway gives a container a fresh filesystem on every deploy, which is fine for
projects — they are rebuildable — but not for heroes. A hero is a photo the user
uploaded and cannot regenerate, so it is the one thing here that has to outlive
the container. Point `DATABASE_URL` at a Postgres instance and heroes move
there; everything else stays in SQLite beside the render output, where it
belongs.

Only heroes live here on purpose. Jobs carry large log arrays and are written on
every progress tick, and pushing that across a network round trip would slow the
render down to no benefit — they die with the container either way.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS heroes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    mime        TEXT NOT NULL DEFAULT 'image/png',
    ext         TEXT NOT NULL DEFAULT '.png',
    image       BYTEA NOT NULL,
    created_at  TEXT NOT NULL
);
"""

_COLS = "id, name, description, mime, ext, created_at"
_pool = None
_ready = False


def enabled() -> bool:
    return bool(config.DATABASE_URL)


def _driver():
    try:
        import psycopg  # noqa: F401
        from psycopg.rows import dict_row

        return dict_row
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "DATABASE_URL is set but the 'psycopg' driver is not installed. "
            "Add psycopg[binary,pool] to requirements.txt."
        ) from exc


@contextmanager
def _conn() -> Iterator[Any]:
    """A pooled connection, with the table created on first use.

    The pool opens lazily rather than at import. Startup must not depend on a
    network service being reachable: a database that is briefly down should make
    the hero library fail, not stop the container from answering its healthcheck.
    """
    global _pool, _ready

    dict_row = _driver()
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            config.DATABASE_URL, min_size=0, max_size=4, timeout=15,
            kwargs={"row_factory": dict_row}, open=True,
        )

    with _pool.connection() as conn:
        if not _ready:
            conn.execute(_SCHEMA)
            _ready = True
        yield conn


def health() -> tuple[bool, str]:
    """Is the hero database actually reachable? Shown on the settings page."""
    if not enabled():
        return False, "not configured"
    try:
        with _conn() as conn:
            conn.execute("SELECT 1").fetchone()
        return True, "connected"
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return False, str(exc)[:200]


# --- heroes ------------------------------------------------------------------

def add_hero(hero_id: str, name: str, description: str, image: bytes,
             mime: str, ext: str, created_at: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO heroes (id, name, description, mime, ext, image, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
            (hero_id, name, description, mime, ext, image, created_at),
        )


def list_heroes() -> list[dict[str, Any]]:
    with _conn() as conn:
        return conn.execute(
            f"SELECT {_COLS} FROM heroes ORDER BY created_at DESC").fetchall()


def get_heroes(hero_ids: list[str]) -> list[dict[str, Any]]:
    if not hero_ids:
        return []
    with _conn() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM heroes WHERE id = ANY(%s)", (list(hero_ids),)).fetchall()
    by_id = {r["id"]: r for r in rows}
    return [by_id[h] for h in hero_ids if h in by_id]


def get_hero_image(hero_id: str) -> tuple[bytes, str, str] | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT image, mime, ext FROM heroes WHERE id = %s", (hero_id,)).fetchone()
    return (bytes(row["image"]), row["mime"], row["ext"]) if row else None


def update_hero(hero_id: str, *, name: str | None = None,
                description: str | None = None) -> bool:
    fields, values = [], []
    if name is not None:
        fields.append("name = %s")
        values.append(name)
    if description is not None:
        fields.append("description = %s")
        values.append(description)
    if not fields:
        return False
    values.append(hero_id)
    with _conn() as conn:
        cur = conn.execute(f"UPDATE heroes SET {', '.join(fields)} WHERE id = %s", values)
        return cur.rowcount > 0


def delete_hero(hero_id: str) -> bool:
    with _conn() as conn:
        return conn.execute("DELETE FROM heroes WHERE id = %s", (hero_id,)).rowcount > 0


def known_ids() -> set[str]:
    with _conn() as conn:
        return {r["id"] for r in conn.execute("SELECT id FROM heroes").fetchall()}
