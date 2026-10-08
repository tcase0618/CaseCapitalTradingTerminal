"""Exchange-calendar US trading-date helpers for research accounting."""
from __future__ import annotations

from datetime import date, timedelta


def is_trading_day(value: date) -> bool:
    from .kronos_contract import exchange
    return value.isoformat() in exchange(value.year).schedule.index


def is_core_session(now) -> bool:
    from .kronos_contract import exchange
    from zoneinfo import ZoneInfo
    day = now.astimezone(ZoneInfo("America/New_York")).date()
    schedule = exchange(day.year).schedule
    if day.isoformat() not in schedule.index:
        return False
    row = schedule.loc[day.isoformat()]
    return row["open"] <= now <= row["close"]


def add_trading_days(value: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = value
    while remaining:
        current += timedelta(days=step)
        if is_trading_day(current):
            remaining -= 1
    return current


def trading_days_between(start: date, end: date) -> int:
    if end < start:
        return -trading_days_between(end, start)
    current = start
    count = 0
    while current < end:
        current += timedelta(days=1)
        if is_trading_day(current):
            count += 1
    return count
