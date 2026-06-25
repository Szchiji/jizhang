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
                project_name TEXT NOT NULL,
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
            ALTER TABLE entries
            ADD COLUMN IF NOT EXISTS project_name TEXT
        """)
        await conn.execute(
            "UPDATE entries SET project_name = $1 WHERE project_name IS NULL OR BTRIM(project_name) = ''",
            config.DEFAULT_PROJECT_NAME,
        )
        await conn.execute("""
            ALTER TABLE entries
            ALTER COLUMN project_name SET NOT NULL
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_user_project
            ON entries (forward_uid, project_name)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entries_date_project
            ON entries (date_local, project_name)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                keyword    TEXT    PRIMARY KEY,
                user_id    BIGINT NOT NULL,
                owner_user_id BIGINT NOT NULL DEFAULT 0,
                created_by BIGINT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
        """)
        await conn.execute("""
            ALTER TABLE aliases
            ADD COLUMN IF NOT EXISTS owner_user_id BIGINT
        """)
        await conn.execute(
            "UPDATE aliases SET owner_user_id = 0 WHERE owner_user_id IS NULL"
        )
        await conn.execute("""
            ALTER TABLE aliases
            ALTER COLUMN owner_user_id SET NOT NULL
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'aliases_pkey'
                ) THEN
                    ALTER TABLE aliases DROP CONSTRAINT aliases_pkey;
                END IF;
            END$$
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_aliases_keyword_owner
            ON aliases (keyword, owner_user_id)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS project_aliases (
                keyword      TEXT    PRIMARY KEY,
                project_name TEXT    NOT NULL,
                owner_user_id BIGINT NOT NULL DEFAULT 0,
                created_by   BIGINT  NOT NULL,
                created_at   TIMESTAMPTZ NOT NULL
            )
        """)
        await conn.execute("""
            ALTER TABLE project_aliases
            ADD COLUMN IF NOT EXISTS owner_user_id BIGINT
        """)
        await conn.execute(
            "UPDATE project_aliases SET owner_user_id = 0 WHERE owner_user_id IS NULL"
        )
        await conn.execute("""
            ALTER TABLE project_aliases
            ALTER COLUMN owner_user_id SET NOT NULL
        """)
        await conn.execute("""
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'project_aliases_pkey'
                ) THEN
                    ALTER TABLE project_aliases DROP CONSTRAINT project_aliases_pkey;
                END IF;
            END$$
        """)
        await conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_project_aliases_keyword_owner
            ON project_aliases (keyword, owner_user_id)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS allowed_users (
                user_id    BIGINT PRIMARY KEY,
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
    project_name: str,
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
                   (forward_uid, forward_name, project_name, amount, chat_id, message_id,
                    source_hash, created_at, date_local)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                forward_uid,
                forward_name,
                project_name,
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


async def get_running_total_for_source(
    *,
    forward_uid: Optional[int],
    forward_name: Optional[str],
) -> float:
    """Return cumulative amount for a source user/name."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if forward_uid is not None:
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE forward_uid = $1",
                forward_uid,
            )
        elif forward_name:
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(amount), 0) FROM entries WHERE forward_name = $1",
                forward_name,
            )
        else:
            total = 0
    finally:
        await conn.close()
    return float(total or 0)


async def clear_entries_by_forward_uid(forward_uid: int) -> int:
    """Delete all entries matching *forward_uid* and return deleted count."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        result = await conn.execute(
            "DELETE FROM entries WHERE forward_uid = $1",
            forward_uid,
        )
    finally:
        await conn.close()
    return int(result.split()[-1])


async def clear_entries_by_forward_uid_and_project(forward_uid: int, project_name: str) -> int:
    """Delete entries matching *forward_uid* and *project_name*."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        result = await conn.execute(
            "DELETE FROM entries WHERE forward_uid = $1 AND project_name = $2",
            forward_uid,
            project_name,
        )
    finally:
        await conn.close()
    return int(result.split()[-1])


# ── Alias operations ───────────────────────────────────────────────────────────


async def set_alias(
    keyword: str,
    user_id: int,
    created_by: int,
    owner_user_id: int = 0,
) -> None:
    """Upsert a keyword → user_id mapping."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute(
            """INSERT INTO aliases (keyword, user_id, owner_user_id, created_by, created_at)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT(keyword, owner_user_id) DO UPDATE
               SET user_id = EXCLUDED.user_id,
                   created_by = EXCLUDED.created_by,
                   created_at = EXCLUDED.created_at""",
            keyword,
            user_id,
            owner_user_id,
            created_by,
            datetime.now(config.TZ),
        )
    finally:
        await conn.close()


async def get_alias(keyword: str, owner_user_id: Optional[int] = None) -> Optional[int]:
    """Return the user_id bound to *keyword*, or ``None``."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if owner_user_id is None:
            row = await conn.fetchrow(
                "SELECT user_id FROM aliases WHERE keyword = $1 AND owner_user_id = 0",
                keyword,
            )
        else:
            row = await conn.fetchrow(
                """SELECT user_id
                   FROM aliases
                   WHERE keyword = $1
                    AND owner_user_id IN (0, $2)
                   ORDER BY (owner_user_id = $2) DESC
                   LIMIT 1""",
                keyword,
                owner_user_id,
            )
    finally:
        await conn.close()
    return row["user_id"] if row else None


async def get_alias_keyword_for_user(
    user_id: int, owner_user_id: Optional[int] = None
) -> Optional[str]:
    """Return one keyword bound to *user_id*, preferring the current owner's scope."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if owner_user_id is None:
            row = await conn.fetchrow(
                """SELECT keyword
                   FROM aliases
                   WHERE user_id = $1
                     AND owner_user_id = 0
                   ORDER BY LENGTH(keyword) DESC, created_at DESC
                   LIMIT 1""",
                user_id,
            )
        else:
            row = await conn.fetchrow(
                """SELECT keyword
                   FROM aliases
                   WHERE user_id = $1
                     AND owner_user_id IN (0, $2)
                   ORDER BY (owner_user_id = $2) DESC, LENGTH(keyword) DESC, created_at DESC
                   LIMIT 1""",
                user_id,
                owner_user_id,
            )
    finally:
        await conn.close()
    return row["keyword"] if row else None


async def list_aliases(owner_user_id: Optional[int] = None) -> list[tuple[str, int]]:
    """Return all (keyword, user_id) pairs sorted by keyword."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if owner_user_id is None:
            rows = await conn.fetch(
                "SELECT keyword, user_id FROM aliases WHERE owner_user_id = 0 ORDER BY keyword"
            )
        else:
            rows = await conn.fetch(
                "SELECT keyword, user_id FROM aliases WHERE owner_user_id = $1 ORDER BY keyword",
                owner_user_id,
            )
    finally:
        await conn.close()
    return [(row["keyword"], row["user_id"]) for row in rows]


async def remove_alias(keyword: str, owner_user_id: int = 0) -> bool:
    """Remove one keyword → user mapping under the given owner scope."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        result = await conn.execute(
            "DELETE FROM aliases WHERE keyword = $1 AND owner_user_id = $2",
            keyword,
            owner_user_id,
        )
    finally:
        await conn.close()
    return int(result.split()[-1]) > 0


async def set_project_alias(
    keyword: str,
    project_name: str,
    created_by: int,
    owner_user_id: int = 0,
) -> None:
    """Upsert a keyword → project_name mapping."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute(
            """INSERT INTO project_aliases (keyword, project_name, owner_user_id, created_by, created_at)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT(keyword, owner_user_id) DO UPDATE
               SET project_name = EXCLUDED.project_name,
                   created_by = EXCLUDED.created_by,
                   created_at = EXCLUDED.created_at""",
            keyword,
            project_name,
            owner_user_id,
            created_by,
            datetime.now(config.TZ),
        )
    finally:
        await conn.close()


async def list_project_aliases(owner_user_id: Optional[int] = None) -> list[tuple[str, str]]:
    """Return visible (keyword, project_name) pairs sorted by keyword."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if owner_user_id is None:
            rows = await conn.fetch(
                "SELECT keyword, project_name FROM project_aliases WHERE owner_user_id = 0 ORDER BY keyword"
            )
        else:
            rows = await conn.fetch(
                "SELECT keyword, project_name FROM project_aliases WHERE owner_user_id = $1 ORDER BY keyword",
                owner_user_id,
            )
    finally:
        await conn.close()
    return [(row["keyword"], row["project_name"]) for row in rows]


async def remove_project_alias(keyword: str, owner_user_id: int = 0) -> bool:
    """Remove one keyword → project mapping under the given owner scope."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        result = await conn.execute(
            "DELETE FROM project_aliases WHERE keyword = $1 AND owner_user_id = $2",
            keyword,
            owner_user_id,
        )
    finally:
        await conn.close()
    return int(result.split()[-1]) > 0


async def resolve_project_by_text(text: str, owner_user_id: Optional[int] = None) -> Optional[str]:
    """Return project from the best-matching keyword found in *text*."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        if owner_user_id is None:
            row = await conn.fetchrow(
                """SELECT project_name
                   FROM project_aliases
                   WHERE owner_user_id = 0
                    AND $1 ILIKE ('%%' || keyword || '%%')
                   ORDER BY LENGTH(keyword) DESC
                   LIMIT 1""",
                text,
            )
        else:
            row = await conn.fetchrow(
                """SELECT project_name
                   FROM project_aliases
                   WHERE owner_user_id IN (0, $2)
                    AND $1 ILIKE ('%%' || keyword || '%%')
                   ORDER BY (owner_user_id = $2) DESC, LENGTH(keyword) DESC
                   LIMIT 1""",
                text,
                owner_user_id,
            )
    finally:
        await conn.close()
    return row["project_name"] if row else None


async def upsert_allowed_user(user_id: int, created_by: int) -> None:
    """Add/update an allowed user."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        await conn.execute(
            """INSERT INTO allowed_users (user_id, created_by, created_at)
               VALUES ($1, $2, $3)
               ON CONFLICT(user_id) DO UPDATE
               SET created_by = EXCLUDED.created_by,
                   created_at = EXCLUDED.created_at""",
            user_id,
            created_by,
            datetime.now(config.TZ),
        )
    finally:
        await conn.close()


async def remove_allowed_user(user_id: int) -> bool:
    """Remove an allowed user. Returns True when deleted."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        result = await conn.execute(
            "DELETE FROM allowed_users WHERE user_id = $1",
            user_id,
        )
    finally:
        await conn.close()
    return int(result.split()[-1]) > 0


async def list_allowed_users() -> list[int]:
    """Return all allowed user IDs sorted ascending."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT user_id FROM allowed_users ORDER BY user_id"
        )
    finally:
        await conn.close()
    return [int(row["user_id"]) for row in rows]


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
        project_rows = await conn.fetch(
            """SELECT project_name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE date_local = $1
               GROUP BY project_name
               ORDER BY SUM(amount) DESC""",
            target_date,
        )
    finally:
        await conn.close()

    persons = [(row["name"], float(row["total"]), row["cnt"]) for row in persons_rows]
    projects = [(row["project_name"], float(row["total"]), row["cnt"]) for row in project_rows]
    return {"count": count, "total": total, "persons": persons, "projects": projects}


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
        project_rows = await conn.fetch(
            """SELECT project_name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE EXTRACT(YEAR FROM date_local) = $1
                 AND EXTRACT(MONTH FROM date_local) = $2
               GROUP BY project_name
               ORDER BY SUM(amount) DESC""",
            year,
            month,
        )
    finally:
        await conn.close()

    persons = [(row["name"], float(row["total"]), row["cnt"]) for row in persons_rows]
    projects = [(row["project_name"], float(row["total"]), row["cnt"]) for row in project_rows]
    return {"count": count, "total": total, "persons": persons, "projects": projects}


async def get_monthly_stats_for_user(year: int, month: int, forward_uid: int) -> dict:
    """Return monthly statistics for a single forwarded user ID."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """SELECT COUNT(*), COALESCE(SUM(amount), 0)
               FROM entries
               WHERE EXTRACT(YEAR FROM date_local) = $1
                 AND EXTRACT(MONTH FROM date_local) = $2
                 AND forward_uid = $3""",
            year,
            month,
            forward_uid,
        )
        count, total = row[0], float(row[1])

        project_rows = await conn.fetch(
            """SELECT project_name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE EXTRACT(YEAR FROM date_local) = $1
                 AND EXTRACT(MONTH FROM date_local) = $2
                 AND forward_uid = $3
               GROUP BY project_name
               ORDER BY SUM(amount) DESC""",
            year,
            month,
            forward_uid,
        )
    finally:
        await conn.close()

    projects = [(row["project_name"], float(row["total"]), row["cnt"]) for row in project_rows]
    return {"count": count, "total": total, "projects": projects}


async def get_daily_stats_for_user(target_date: date, forward_uid: int) -> dict:
    """Return daily statistics for a single forwarded user ID."""
    conn = await asyncpg.connect(config.DATABASE_URL)
    try:
        row = await conn.fetchrow(
            """SELECT COUNT(*), COALESCE(SUM(amount), 0)
               FROM entries
               WHERE date_local = $1
                 AND forward_uid = $2""",
            target_date,
            forward_uid,
        )
        count, total = row[0], float(row[1])

        project_rows = await conn.fetch(
            """SELECT project_name, SUM(amount) AS total, COUNT(*) AS cnt
               FROM entries
               WHERE date_local = $1
                 AND forward_uid = $2
               GROUP BY project_name
               ORDER BY SUM(amount) DESC""",
            target_date,
            forward_uid,
        )
    finally:
        await conn.close()

    projects = [(row["project_name"], float(row["total"]), row["cnt"]) for row in project_rows]
    return {"count": count, "total": total, "projects": projects}
