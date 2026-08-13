from services import telegram_events


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
