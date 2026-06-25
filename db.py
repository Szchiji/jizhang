"""Async SQLite database layer using aiosqlite."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, date
from typing import Optional

import aiosqlite

import config

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    """Current time as ISO-8601 string in the configured timezone."""
    return datetime.now(config.TZ).isoformat()


def _local_date() -> str:
    """Current local date as YYYY-MM-DD."""
    return datetime.now(config.TZ).strftime("%Y-%m-%d")


# ── Initialisation ─────────────────────────────────────────────────────────────


async def init_db() -> None:
    """Create tables and indexes if they do not already exist."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                forward_uid INTEGER,
                forward_name TEXT,
                amount      REAL    NOT NULL,
                chat_id     INTEGER NOT NULL,
                message_id  INTEGER NOT NULL,
                source_hash TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                date_local  TEXT    NOT NULL
            )
        """)
        await db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source
            ON entries (source_hash)
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                keyword    TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT    NOT NULL
            )
        """)
        await db.commit()
    logger.info("Database ready at %s", config.DATABASE_PATH)


# ── Entry operations ───────────────────────────────────────────────────────────


async def insert_entry(
    *,
    forward_uid: Optional[int],
    forward_name: Optional[str],
    amount: float,
    chat_id: int,
    message_id: int,
    source_hash: str,
) -> bool:
    """Insert one bookkeeping entry.

    Returns ``True`` when inserted successfully, ``False`` when the
    *source_hash* already exists (duplicate forward).
    """
    try:
        async with aiosqlite.connect(config.DATABASE_PATH) as db:
            await db.execute(
                """INSERT INTO entries
                   (forward_uid, forward_name, amount, chat_id, message_id,
                    source_hash, created_at, date_local)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    forward_uid,
                    forward_name,
                    amount,
                    chat_id,
                    message_id,
                    source_hash,
                    _now_iso(),
                    _local_date(),
                ),
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


# ── Alias operations ───────────────────────────────────────────────────────────


async def set_alias(keyword: str, user_id: int, created_by: int) -> None:
    """Upsert a keyword → user_id mapping."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO aliases (keyword, user_id, created_by, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(keyword) DO UPDATE
               SET user_id    = excluded.user_id,
                   created_by = excluded.created_by,
                   created_at = excluded.created_at""",
            (keyword, user_id, created_by, _now_iso()),
        )
        await db.commit()


async def get_alias(keyword: str) -> Optional[int]:
    """Return the user_id bound to *keyword*, or ``None``."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT user_id FROM aliases WHERE keyword = ?", (keyword,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None


async def list_aliases() -> list[tuple[str, int]]:
    """Return all (keyword, user_id) pairs sorted by keyword."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT keyword, user_id FROM aliases ORDER BY keyword"
        ) as cur:
            return await cur.fetchall()


# ── Statistics ─────────────────────────────────────────────────────────────────


async def get_daily_stats(target_date: date) -> dict:
    """Return bookkeeping statistics for *target_date* (local date)."""
    date_str = target_date.strftime("%Y-%m-%d")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM entries WHERE date_local = ?",
            (date_str,),
        ) as cur:
            count, total = await cur.fetchone()

        async with db.execute(
            """SELECT COALESCE(forward_name, '未知'), SUM(amount), COUNT(*)
               FROM entries
               WHERE date_local = ?
               GROUP BY forward_uid, forward_name
               ORDER BY SUM(amount) DESC""",
            (date_str,),
        ) as cur:
            persons = await cur.fetchall()

    return {"count": count, "total": total, "persons": persons}


async def get_monthly_stats(year: int, month: int) -> dict:
    """Return bookkeeping statistics for the given year/month."""
    prefix = f"{year:04d}-{month:02d}"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM entries WHERE date_local LIKE ?",
            (f"{prefix}%",),
        ) as cur:
            count, total = await cur.fetchone()

        async with db.execute(
            """SELECT COALESCE(forward_name, '未知'), SUM(amount), COUNT(*)
               FROM entries
               WHERE date_local LIKE ?
               GROUP BY forward_uid, forward_name
               ORDER BY SUM(amount) DESC""",
            (f"{prefix}%",),
        ) as cur:
            persons = await cur.fetchall()

    return {"count": count, "total": total, "persons": persons}
