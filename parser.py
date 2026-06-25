"""Extract candidate monetary amounts from free-form text.

Numbers that look like dates, times, phone numbers, order IDs or
percentages are filtered out so only genuine monetary values remain.
"""
from __future__ import annotations

import re

# ── Patterns whose numeric content must NOT be treated as amounts ──────────────
# Use (?<!\d)/(?!\d) instead of \b so they work adjacent to Chinese characters.
_EXCLUDE: list[re.Pattern[str]] = [
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),                    # Chinese mobile (11 digits)
    re.compile(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"),       # HH:MM or HH:MM:SS
    re.compile(r"(?<!\d)\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[日号]?"),# YYYY-MM-DD (CN/ISO)
    re.compile(r"(?<!\d)\d{1,2}[-/]\d{1,2}[-/]\d{2,4}(?!\d)"),  # DD/MM/YYYY
    re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)"),                  # 4-digit year 1900–2099
    re.compile(r"(?<!\d)\d{11,}(?!\d)"),                         # long IDs / order numbers
    re.compile(r"\d+(?:\.\d+)?%"),                               # percentages
    re.compile(r"(?<!\d)\d{3,4}-\d{4}(?!\d)"),                   # phone xxx-xxxx
]

# Pattern that captures a candidate amount (with optional currency prefix).
# Uses (?<!\d)/(?!\d) so it matches digits embedded in Chinese text.
_AMOUNT_RE = re.compile(
    r"[¥￥$€£]\s*\d{1,3}(?:[,，]\d{3})*(?:\.\d{1,2})?"          # currency-prefixed
    r"|(?<!\d)\d{1,3}(?:[,，]\d{3})+(?:\.\d{1,2})?(?!\d)"       # thousands-grouped
    r"|(?<!\d)\d+\.\d{1,2}(?!\d)"                                # decimal N.NN
    r"|(?<!\d)\d+(?!\d)"                                         # plain integer
)

_PROJECT_TAG_RE = re.compile(
    r"(?:^|\s)#([A-Za-z0-9_\-\u4e00-\u9fff]{1,32})(?=\s|$)"
)
_PROJECT_KEYVAL_RE = re.compile(
    r"项目\s*[:：]\s*([A-Za-z0-9_\-\u4e00-\u9fff]{1,32})"
)
_PROJECT_PREFIX_RE = re.compile(
    r"(?:^|\s)(项目[A-Za-z0-9_\-\u4e00-\u9fff]{1,31})(?=\s|$)"
)


def extract_amounts(text: str) -> list[float]:
    """Return candidate amounts from *text* in order of appearance.

    Numbers that overlap with date, time, phone or other non-monetary patterns
    are silently discarded.  Only values in the range [0.01, 999 999] are kept.
    """
    if not text:
        return []

    # Build a set of character positions that belong to excluded patterns
    excluded: set[int] = set()
    for pat in _EXCLUDE:
        for m in pat.finditer(text):
            excluded.update(range(m.start(), m.end()))

    amounts: list[float] = []

    for m in _AMOUNT_RE.finditer(text):
        # Reject if the match sits inside an excluded zone
        if any(i in excluded for i in range(m.start(), m.end())):
            continue

        # Strip currency symbols and digit-group separators, then parse
        raw = re.sub(r"[¥￥$€£\s,，]", "", m.group())
        try:
            value = float(raw)
        except ValueError:
            continue

        if 0.01 <= value <= 999_999:
            amounts.append(value)

    return amounts


def extract_project_name(text: str) -> str | None:
    """Extract project name from text using explicit project markers."""
    if not text:
        return None

    for pat in (_PROJECT_TAG_RE, _PROJECT_KEYVAL_RE, _PROJECT_PREFIX_RE):
        m = pat.search(text)
        if m:
            return m.group(1).strip()

    return None
