"""
Shared utilities — MarkdownV2 escaping and formatting helpers.

RULES:
  - escape_md(s)  : escapes raw user/data text for safe embedding in MarkdownV2
  - bold/italic   : take RAW strings, escape internally — never pass pre-escaped text
  - code(s)       : backtick content is literal in MarkdownV2, so NO escaping inside
  - italic_raw(s) : takes an already-escaped string (for inline use in f-strings)
  - All send_message calls must use parse_mode="MarkdownV2"
"""

import re
from datetime import datetime
from typing import Optional


def escape_md(text) -> str:
    """Escape all MarkdownV2 special characters. Always call on raw user/DB text."""
    if not text:
        return ""
    return re.sub(r"([\_\*\[\]\(\)\~\`\>\#\+\-\=\|\{\}\.\!])", r"\\\1", str(text))


def bold(text: str) -> str:
    """Wrap raw text in MarkdownV2 bold."""
    return f"*{escape_md(text)}*"


def italic(text: str) -> str:
    """Wrap raw text in MarkdownV2 italic."""
    return f"_{escape_md(text)}_"


def code(text: str) -> str:
    """
    Wrap text in MarkdownV2 inline code backticks.
    Content inside backticks is LITERAL in MarkdownV2 — do NOT escape it.
    """
    return f"`{text}`"


def link(label: str, url: str) -> str:
    """MarkdownV2 hyperlink. Label is raw text; URL must be a valid URL (parens not escaped)."""
    return f"[{escape_md(label)}]({url})"


def divider() -> str:
    return "━━━━━━━━━━━━━━━━━━━━"


def fmt_dt(iso: str) -> str:
    """Format ISO datetime string to readable form. Returns raw string (call escape_md if embedding)."""
    if not iso:
        return "—"
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d %b %Y, %I:%M %p")
    except Exception:
        return iso[:16]


STATUS_LABEL = {
    "pending":  "🟡 Pending",
    "done":     "🔵 Awaiting Verify",
    "verified": "✅ Verified",
    "rejected": "❌ Rejected",
}

PRIORITY_LABEL = {
    "low":    "🟢 Low",
    "normal": "🔵 Normal",
    "high":   "🔴 High",
}
