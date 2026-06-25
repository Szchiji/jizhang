"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from datetime import time as dt_time
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

# ── Timezone ───────────────────────────────────────────────────────────────────
TZ: ZoneInfo = ZoneInfo(os.environ.get("TZ", "Asia/Shanghai"))

# ── Reports ────────────────────────────────────────────────────────────────────
# Chat ID where daily/monthly reports are sent (0 = disabled)
REPORT_CHAT_ID: int = int(os.environ.get("REPORT_CHAT_ID", "0"))


def _parse_report_time(name: str, default: str) -> dt_time:
    """Parse HH:MM report time config."""
    raw = os.environ.get(name, default).strip()
    try:
        hour_text, minute_text = raw.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be HH:MM, got: {raw!r}") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{name} must be HH:MM, got: {raw!r}")
    return dt_time(hour, minute, 0, tzinfo=TZ)


DAILY_REPORT_TIME: dt_time = _parse_report_time("DAILY_REPORT_TIME", "21:00")
MONTHLY_REPORT_TIME: dt_time = _parse_report_time("MONTHLY_REPORT_TIME", "21:00")
MONTHLY_REPORT_DAY: int = int(os.environ.get("MONTHLY_REPORT_DAY", "1"))
if not (1 <= MONTHLY_REPORT_DAY <= 31):
    raise ValueError("MONTHLY_REPORT_DAY must be between 1 and 31")

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

# ── Webhook ─────────────────────────────────────────────────────────────────────
_default_public_domain = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
_default_webhook_base = (
    f"https://{_default_public_domain}" if _default_public_domain else ""
)
WEBHOOK_BASE_URL: str = os.environ.get("WEBHOOK_BASE_URL", _default_webhook_base).rstrip("/")
WEBHOOK_PATH: str = os.environ.get("WEBHOOK_PATH", "/telegram/webhook").strip() or "/telegram/webhook"
if not WEBHOOK_PATH.startswith("/"):
    WEBHOOK_PATH = f"/{WEBHOOK_PATH}"
WEBHOOK_URL: str = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}" if WEBHOOK_BASE_URL else ""
WEBHOOK_LISTEN: str = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
WEBHOOK_PORT: int = int(os.environ.get("PORT", os.environ.get("WEBHOOK_PORT", "8080")))
WEBHOOK_SECRET_TOKEN: str = os.environ.get("WEBHOOK_SECRET_TOKEN", "").strip()

# ── Bookkeeping project dimension ────────────────────────────────────────────────
# Fallback project name when no explicit project can be parsed from message text.
DEFAULT_PROJECT_NAME: str = os.environ.get("DEFAULT_PROJECT_NAME", "默认项目")
