"""Small, dependency-free US trading-date helpers for research accounting."""
from __future__ import annotations

from datetime import date, timedelta


def add_trading_days(value: date, days: int) -> date:
    step = 1 if days >= 0 else -1
    remaining = abs(days)
    current = value
    while remaining:
        current += timedelta(days=step)
        if current.weekday() < 5:
            remaining -= 1
    return current


def trading_days_between(start: date, end: date) -> int:
    if end < start:
        return -trading_days_between(end, start)
    current = start
    count = 0
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            count += 1
    return count
