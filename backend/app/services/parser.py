from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from dateparser.search import search_dates


LEADING_TRIGGER_RE = re.compile(r"^(напомни(?:\s+мне)?|remind\s+me)\s+", re.IGNORECASE)


def extract_reminder_payload(raw_text: str, timezone_name: str) -> tuple[str, datetime]:
    timezone = ZoneInfo(timezone_name)
    cleaned = LEADING_TRIGGER_RE.sub("", raw_text.strip())
    matches = search_dates(
        cleaned,
        languages=["ru", "en"],
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": datetime.now(timezone),
            "TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )
    if not matches:
        raise ValueError("Не удалось распознать дату. Пример: завтра в 15:00 оплатить подписку.")

    date_phrase, due_at = matches[0]
    reminder_text = cleaned.replace(date_phrase, " ", 1)
    reminder_text = re.sub(r"\s{2,}", " ", reminder_text).strip(" ,.-")
    if not reminder_text:
        raise ValueError("После даты должен остаться текст напоминания.")

    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=timezone)
    return reminder_text, due_at.astimezone(timezone)

