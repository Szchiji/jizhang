"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

# ── Required ───────────────────────────────────────────────────────────────────
BOT_TOKEN: str = os.environ["BOT_TOKEN"]

# ── Access control ─────────────────────────────────────────────────────────────
# Comma-separated Telegram user IDs with admin privileges
ADMIN_IDS: list[int] = [
    int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()
]

# Optional whitelists (empty list = no restriction)
ALLOWED_USER_IDS: list[int] = [
    int(x) for x in os.environ.get("ALLOWED_USER_IDS", "").split(",") if x.strip()
]
ALLOWED_CHAT_IDS: list[int] = [
    int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",") if x.strip()
]

# ── Reports ────────────────────────────────────────────────────────────────────
# Chat ID where daily/monthly reports are sent (0 = disabled)
REPORT_CHAT_ID: int = int(os.environ.get("REPORT_CHAT_ID", "0"))

# ── Storage ────────────────────────────────────────────────────────────────────
def _resolve_database_url() -> str:
    """Resolve PostgreSQL URL from common env var names."""
    for key in (
        "DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "DATABASE_PUBLIC_URL",
        "POSTGRES_URL",
        "POSTGRESQL_URL",
    ):
        value = os.environ.get(key)
        if value:
            return value
    return "postgresql://localhost/jizhang"


# PostgreSQL connection string.
# Example: postgresql://postgres@localhost:5432/jizhang
DATABASE_URL: str = _resolve_database_url()

# ── Timezone ───────────────────────────────────────────────────────────────────
TZ: ZoneInfo = ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))
