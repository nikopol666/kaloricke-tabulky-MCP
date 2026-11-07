"""One timezone-aware clock for public dates and diary meal selection."""

from datetime import date, datetime
import os
from zoneinfo import ZoneInfo


def timezone_info(name: str | None = None) -> ZoneInfo:
    return ZoneInfo(
        name if name is not None else os.getenv("KT_TIMEZONE", "Europe/Prague")
    )


def local_now(timezone: str | None = None) -> datetime:
    return datetime.now(timezone_info(timezone))


def local_today(timezone: str | None = None) -> date:
    return local_now(timezone).date()
