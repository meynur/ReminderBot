from __future__ import annotations

from datetime import datetime, timedelta


def make_schedule_meta(schedule_type: str, start_at: datetime) -> dict | None:
    if schedule_type == "once":
        return None
    if schedule_type == "daily":
        return {"hour": start_at.hour, "minute": start_at.minute}
    if schedule_type == "weekly":
        return {"weekday": start_at.weekday(), "hour": start_at.hour, "minute": start_at.minute}
    if schedule_type == "monthly":
        return {"day": start_at.day, "hour": start_at.hour, "minute": start_at.minute}
    raise ValueError(f"Неподдерживаемый тип расписания: {schedule_type}")


def compute_next_run(schedule_type: str, current_run: datetime) -> datetime | None:
    if schedule_type == "once":
        return None

    if schedule_type == "daily":
        return current_run + timedelta(days=1)

    if schedule_type == "weekly":
        return current_run + timedelta(days=7)

    if schedule_type == "monthly":
        year = current_run.year
        month = current_run.month
        day = current_run.day
        if month == 12:
            target_year = year + 1
            target_month = 1
        else:
            target_year = year
            target_month = month + 1

        target_day = min(day, _days_in_month(target_year, target_month))
        return current_run.replace(year=target_year, month=target_month, day=target_day)

    raise ValueError(f"Неподдерживаемый тип расписания: {schedule_type}")


def describe_schedule(schedule_type: str) -> str:
    labels = {
        "once": "Один раз",
        "daily": "Каждый день",
        "weekly": "Каждую неделю",
        "monthly": "Каждый месяц",
    }
    return labels.get(schedule_type, schedule_type)


def _days_in_month(year: int, month: int) -> int:
    if month == 2:
        if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
            return 29
        return 28
    if month in {4, 6, 9, 11}:
        return 30
    return 31
