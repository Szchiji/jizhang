"""Async PostgreSQL database layer using asyncpg."""
from __future__ import annotations

import logging
from datetime import datetime, date
from typing import Optional

import asyncpg

import config

logger = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _local_date() -> date:
    """Current local date."""
    return datetime.now(config.TZ).date()


# ── Initialisation ─────────────────────────────────────────────────────────────


async def init_db() -> None:
    """Create tables and indexes if they do not already exist."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id           BIGSERIAL PRIMARY KEY,
                forward_uid  BIGINT,
                forward_name TEXT,
                amount       NUMERIC(18,2) NOT NULL,
                chat_id      BIGINT NOT NULL,
                message_id   BIGINT NOT NULL,
                source_hash  TEXT NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL,
                date_local   DATE NOT NULL
            )
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_source
            ON entries (source_hash)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                keyword    TEXT    PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
        """)
    finally:
        await conn.close()
    logger.info("Database ready at %s", config.DATABASE_URL)


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
        conn = await asyncpg.connect(config.DATABASE_URL)
        try:
            await conn.execute(
                """INSERT INTO entries
                   (forward_uid, forward_name, amount, chat_id, message_id,
                    source_hash, created_at, date_local)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                forward_uid,
                forward_name,
                amount,
                chat_id,
                message_id,
                source_hash,
                datetime.now(config.TZ),
                _local_date(),
            )
        finally:
            await conn.close()
        return True
    except asyncpg.UniqueViolationError:
        return False


# ── Alias operations ───────────────────────────────────────────────────────────


async def set_alias(keyword: str, user_id: int, created_by: int) -> None:
    """Upsert a keyword → user_id mapping."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute(
            """INSERT INTO aliases (keyword, user_id, created_by, created_at)
               VALUES ($1, $2, $3, $4)
               ON CONFLICT(keyword) DO UPDATE
               SET user_id = EXCLUDED.user_id,
                   created_by = EXCLUDED.created_by,
                   created_at = EXCLUDED.created_at""",
            keyword,
            user_id,
            created_by,
            datetime.now(config.TZ),
        )
    finally:
        await conn.close()


async def get_alias(keyword: str) -> Optional[int]:
    """Return the user_id bound to *keyword*, or ``None``."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "SELECT user_id FROM aliases WHERE keyword = $1",
            keyword,
        )
    finally:
        await conn.close()
    return row["user_id"] if row else None


async def list_aliases() -> list[tuple[str, int]]:
    """Return all (keyword, user_id) pairs sorted by keyword."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT keyword, user_id FROM aliases ORDER BY keyword"
        )
    finally:
        await conn.close()
    return [(row["keyword"], row["user_id"]) for row in rows]


# ── Statistics ─────────────────────────────────────────────────────────────────


async def get_daily_stats(target_date: date) -> dict:
    """Return bookkeeping statistics for *target_date* (local date)."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await conn.fetchrow(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM entries WHERE date_local = $1",
            target_date,
        )
        count, total = row[0], float(row[1])

        persons_rows = await conn.fetch(
            """SELECT COALESCE(forward_name, '未知') AS name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE date_local = $1
               GROUP BY forward_uid, forward_name
               ORDER BY SUM(amount) DESC""",
            target_date,
        )
    finally:
        await conn.close()

    persons = [(row["name"], float(row["total"]), row["cnt"]) for row in persons_rows]
    return {"count": count, "total": total, "persons": persons}


async def get_monthly_stats(year: int, month: int) -> dict:
    """Return bookkeeping statistics for the given year/month."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """SELECT COUNT(*), COALESCE(SUM(amount), 0)
               FROM entries
               WHERE EXTRACT(YEAR FROM date_local) = $1
                 AND EXTRACT(MONTH FROM date_local) = $2""",
            year,
            month,
        )
        count, total = row[0], float(row[1])

        persons_rows = await conn.fetch(
            """SELECT COALESCE(forward_name, '未知') AS name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE EXTRACT(YEAR FROM date_local) = $1
                 AND EXTRACT(MONTH FROM date_local) = $2
               GROUP BY forward_uid, forward_name
               ORDER BY SUM(amount) DESC""",
            year,
            month,
        )
    finally:
        await conn.close()

    persons = [(row["name"], float(row["total"]), row["cnt"]) for row in persons_rows]
    return {"count": count, "total": total, "persons": persons}
