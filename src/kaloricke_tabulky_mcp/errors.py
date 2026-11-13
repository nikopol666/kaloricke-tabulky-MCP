"""Bounded diagnostics with credential redaction, including DEBUG output."""

import re
from typing import Any, Iterable


def safe_text(value: Any, secrets: Iterable[str] = ()) -> str:
    text = value if isinstance(value, str) else repr(value)
    for secret in sorted({v for v in secrets if v}, key=len, reverse=True):
        text = text.replace(secret, "<redacted>")
    text = re.sub(r"(?i)\b[0-9a-f]{32}\b", "<redacted-hash>", text)
    text = re.sub(
        r"(?i)(['\"]?(?:set-cookie|cookie|authorization|password(?:_md5)?|(?:access_|refresh_)?token|csrf|secret|session(?:_?id)?)['\"]?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;<}\]]+)",
        r"\1<redacted>",
        text,
    )
    return text


def short_error(value: Any, limit: int = 300) -> str:
    text = safe_text(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."
