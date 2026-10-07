"""Research-only, completed RTH bar contracts for forecast inputs and outcomes."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

MODEL_VERSION = "ohlcv_baseline_v2.1"
ET = ZoneInfo("America/New_York")
MINUTES = {"5m": 5, "15m": 15, "1h": 60, "1d": 1440}


def parse_time(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or timezone.utc).astimezone(timezone.utc)
    try:
        return parse_time(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=8)
def exchange(year):
    import exchange_calendars
    return exchange_calendars.get_calendar("XNYS", start=f"{year - 1}-01-01", end=f"{year + 1}-12-31")


def sessions(now):
    cal = exchange(now.year)
    schedule = cal.schedule.loc[(now - timedelta(days=14)).date().isoformat():(now + timedelta(days=14)).date().isoformat()]
    return [(day.date(), row["open"].to_pydatetime(), row["close"].to_pydatetime()) for day, row in schedule.iterrows()]


def bar_close(timestamp, timeframe, now):
    start = parse_time(timestamp)
    if start is None or timeframe not in MINUTES:
        return None
    # Daily bars are date-labelled; midnight UTC is not their completion time.
    day = start.date() if timeframe == "1d" else start.astimezone(ET).date()
    schedule = exchange(day.year).schedule
    label = day.isoformat()
    if label not in schedule.index:
        return None
    row = schedule.loc[label]
    op, cl = row["open"].to_pydatetime(), row["close"].to_pydatetime()
    if timeframe == "1d":
        return cl
    if not op <= start < cl:
        return None
    return min(start + timedelta(minutes=MINUTES[timeframe]), cl)


def input_contract(candles, timeframe, now):
    complete = {}
    for candle in candles:
        close_at = bar_close(candle.get("timestamp"), timeframe, now)
        if close_at and close_at <= now:
            complete[close_at] = candle
    ordered = sorted(complete.items())
    if not ordered:
        return [], {"ok": False, "reason": "no_completed_session_bars"}
    latest, latest_candle = ordered[-1]
    anchor = parse_time(latest_candle.get("timestamp"))
    expected = None
    target = None
    for _, op, cl in sessions(now):
        if timeframe == "1d":
            if cl <= now:
                expected = cl
            if op >= now and target is None:
                target = cl
        else:
            cursor = op
            step = timedelta(minutes=MINUTES[timeframe])
            offset = (cursor - anchor).total_seconds() % step.total_seconds()
            if offset:
                cursor += timedelta(seconds=step.total_seconds() - offset)
            while cursor < cl:
                end = min(cursor + step, cl)
                if end <= now:
                    expected = end
                if cursor >= now and target is None:
                    target = end
                cursor += step
    tolerance = timedelta(minutes=MINUTES[timeframe] if timeframe != "1d" else 0)
    fresh = expected is not None and latest >= expected - tolerance
    return [c for _, c in ordered], {
        "ok": fresh and target is not None,
        "reason": None if fresh else "stale_input_bars",
        "input_asof": latest.isoformat(),
        "expected_input_asof": expected.isoformat() if expected else None,
        "target_at": target.isoformat() if target else None,
        "session": "XNYS_RTH",
        "model_version": MODEL_VERSION,
    }
