"""The Postgres side of storage: connection, dialect, and moving in.

Railway (and any container host) hands the app a fresh filesystem on every
deploy. Without a database that means every hero, every uploaded sound, every
brand setting and every project you have ever made disappears the next time the
service restarts. Point `DATABASE_URL` at Postgres — Supabase is what this is
written and tested against — and all of it moves there instead.

This module deliberately holds no table logic. `store.py` owns the SQL, once,
and runs the same statements against either database; all that lives here is
what the two engines genuinely disagree about: how a connection is opened, how
placeholders are spelled, and which column type holds bytes. Keeping it that way
is the point — two hand-written copies of the same query drift, and the copy
that drifts is always the one you cannot test locally.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from . import config

_pool: Any = None
_ready = False


def enabled() -> bool:
    return bool(config.DATABASE_URL)


def _driver():
    try:
        from psycopg.rows import dict_row

        return dict_row
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise RuntimeError(
            "DATABASE_URL is set but the 'psycopg' driver is not installed. "
            "Add psycopg[binary,pool] to requirements.txt."
        ) from exc


class _Cursor:
    """A psycopg cursor wearing the small part of the sqlite3 API `store` uses."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def fetchone(self) -> Any:
        return self._cursor.fetchone()

    def fetchall(self) -> list[Any]:
        return self._cursor.fetchall()

    def __iter__(self) -> Iterator[Any]:
        return iter(self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class Connection:
    """Makes a psycopg connection answer to the sqlite3 calls `store` makes.

    Only two things are translated. Placeholders: sqlite writes `?`, psycopg
    writes `%s`. And `%` itself, which psycopg reads as the start of a
    placeholder — no query here contains one, and this would corrupt it if one
    ever did, so it is escaped rather than left as a trap.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Any = ()) -> _Cursor:
        return _Cursor(self._conn.execute(translate(sql), tuple(params or ())))

    def executescript(self, script: str) -> None:
        self._conn.execute(script)

    def commit(self) -> None:
        pass  # the pool's context manager commits on a clean exit


def translate(sql: str) -> str:
    return sql.replace("%", "%%").replace("?", "%s")


def blob() -> str:
    """The bytes column type, spelled for whichever engine is in use."""
    return "BYTEA" if enabled() else "BLOB"


@contextmanager
def connect() -> Iterator[Connection]:
    """A pooled connection. The pool opens lazily, never at import.

    Startup must not depend on a network service answering: a database that is
    briefly unreachable should make the library fail, not stop the container
    from passing its healthcheck.
    """
    global _pool

    dict_row = _driver()
    if _pool is None:
        from psycopg_pool import ConnectionPool

        _pool = ConnectionPool(
            config.DATABASE_URL,
            min_size=0,
            max_size=6,
            timeout=20,
            # Supabase's transaction pooler (port 6543) rejects prepared
            # statements, and psycopg starts preparing a query on its fifth
            # run. Every query here is short, so nothing is lost by never
            # preparing — and with it on, the app works for four requests and
            # then breaks, which is the worst way for this to fail.
            kwargs={"row_factory": dict_row, "prepare_threshold": None},
            open=True,
        )

    with _pool.connection() as conn:
        yield Connection(conn)


def mark_ready() -> bool:
    """True the first time it is called, so the schema runs once per process."""
    global _ready
    if _ready:
        return False
    _ready = True
    return True


def reset() -> None:
    """Drop the pool — used by tests that switch databases mid-process."""
    global _pool, _ready
    if _pool is not None:
        try:
            _pool.close()
        except Exception:  # noqa: BLE001 - closing a broken pool is not an error
            pass
    _pool, _ready = None, False


def health() -> tuple[bool, str]:
    """Is the database actually reachable? Shown on the settings page."""
    if not enabled():
        return False, "not configured"
    try:
        with connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return True, "connected"
    except Exception as exc:  # noqa: BLE001 - reported, never raised at the user
        return False, _readable(exc)


def _readable(exc: Exception) -> str:
    """Turn the driver's message into the thing to actually go and fix."""
    text = str(exc).strip() or exc.__class__.__name__
    lowered = text.lower()
    if "password authentication failed" in lowered:
        return ("Parol noto'g'ri — Supabase'dagi ulanish satrini qayta nusxalang "
                "(parolda maxsus belgi bo'lsa, u kodlangan bo'lishi kerak).")
    if "could not translate host name" in lowered or "name or service not known" in lowered:
        return "Host topilmadi — DATABASE_URL'dagi manzilni tekshiring."
    if "network is unreachable" in lowered:
        return ("Manzilga yetib bo'lmadi. Supabase'ning to'g'ridan-to'g'ri ulanishi "
                "faqat IPv6 — o'rniga Session pooler satrini oling.")
    if "prepared statement" in lowered:
        return "Pooler tayyorlangan so'rovlarni qabul qilmadi — ilovani qayta ishga tushiring."
    return text[:200]
