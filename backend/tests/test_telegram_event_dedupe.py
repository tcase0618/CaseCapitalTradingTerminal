import pytest

from services import telegram_events, telegram_service


def test_scan_report_throttle_enabled_for_scheduler_like_triggers():
    assert telegram_events._scan_report_throttle_enabled("scheduler")
    assert telegram_events._scan_report_throttle_enabled("main_scan")
    assert telegram_events._scan_report_throttle_enabled("quality_auto_remediation")
    assert telegram_events._scan_report_throttle_enabled("schedule_watchdog")
    assert telegram_events._scan_report_throttle_enabled("morning_scan")
    assert telegram_events._scan_report_throttle_enabled("")


def test_scan_report_throttle_allows_explicit_admin_dispatches():
    assert not telegram_events._scan_report_throttle_enabled("admin_dashboard")
    assert not telegram_events._scan_report_throttle_enabled("api")
    assert not telegram_events._scan_report_throttle_enabled("telegram_command")


class _UpdateResult:
    def __init__(self, *, upserted_id=None, modified_count=0):
        self.upserted_id = upserted_id
        self.modified_count = modified_count


class _OutboundGuard:
    def __init__(self):
        self.docs = {}

    async def update_one(self, query, update, upsert=False):
        lock_id = query["_id"]
        existing = self.docs.get(lock_id)
        expires_at = (existing or {}).get("expires_at")
        now_limit = query["$or"][0]["expires_at"]["$lte"]
        can_acquire = existing is None or expires_at is None or expires_at <= now_limit
        if not can_acquire:
            return _UpdateResult()
        doc = {"_id": lock_id, **update["$set"]}
        self.docs[lock_id] = doc
        return _UpdateResult(upserted_id=lock_id if existing is None else None, modified_count=0 if existing is None else 1)


class _Db:
    def __init__(self):
        self.telegram_outbound_guard = _OutboundGuard()


@pytest.mark.asyncio
async def test_scan_report_outbound_lock_blocks_second_report_inside_window(monkeypatch):
    db = _Db()
    monkeypatch.setattr(telegram_service, "get_db", lambda: db)

    msg_a = "<b>CASE CAPITAL | SCAN REPORT</b>\nAug 13 18:07 ET\nSCAN..."
    msg_b = "<b>CASE CAPITAL | SCAN REPORT</b>\nAug 13 18:08 ET\nSCAN..."

    first_skip, first_token = await telegram_service._should_skip_outbound(msg_a)
    second_skip, second_token = await telegram_service._should_skip_outbound(msg_b)

    assert first_skip is False
    assert first_token == "telegram_outbound:scan_report:global"
    assert second_skip is True
    assert second_token == "scan_report_cooldown"


@pytest.mark.asyncio
async def test_non_scan_alerts_lock_by_exact_digest(monkeypatch):
    db = _Db()
    monkeypatch.setattr(telegram_service, "get_db", lambda: db)

    msg_a = "1. $MRNA - 73/100 - CASE SCORE 73 - Binary FDA catalyst"
    msg_b = "2. $LDOS - position risk update"

    first_skip, _ = await telegram_service._should_skip_outbound(msg_a)
    second_skip, _ = await telegram_service._should_skip_outbound(msg_b)

    assert first_skip is False
    assert second_skip is False
