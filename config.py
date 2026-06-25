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
# PostgreSQL connection string.
# Example: postgresql://postgres@localhost:5432/jizhang
DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://localhost/jizhang",
)

# ── Timezone ───────────────────────────────────────────────────────────────────
TZ: ZoneInfo = ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))
