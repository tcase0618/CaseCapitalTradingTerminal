from datetime import datetime

from services import scheduler


def _et_timestamp(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return scheduler.ET.localize(datetime(year, month, day, hour, minute))


def test_public_monitor_window_matches_sunday_to_friday_24_5_schedule():
    assert not scheduler.public_monitor_window_open(_et_timestamp(2026, 9, 25, 20, 0))
    assert not scheduler.public_monitor_window_open(_et_timestamp(2026, 9, 26, 12, 0))
    assert not scheduler.public_monitor_window_open(_et_timestamp(2026, 9, 27, 19, 59))
    assert scheduler.public_monitor_window_open(_et_timestamp(2026, 9, 27, 20, 0))
    assert scheduler.public_monitor_window_open(_et_timestamp(2026, 9, 28, 12, 0))
    assert scheduler.public_monitor_window_open(_et_timestamp(2026, 10, 1, 23, 0))
    assert scheduler.public_monitor_window_open(_et_timestamp(2026, 10, 2, 19, 59))


class _FakeScheduler:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.running = False
        self.jobs = []
        _FakeScheduler.instances.append(self)

    def add_job(self, func, trigger, id=None, replace_existing=False, **kwargs):
        self.jobs.append({
            "func": func,
            "trigger": trigger,
            "id": id,
            "replace_existing": replace_existing,
            "kwargs": kwargs,
        })

    def start(self):
        self.running = True

    def shutdown(self, wait=False):
        self.running = False


def test_scheduler_uses_non_overlapping_job_defaults(monkeypatch):
    _FakeScheduler.instances = []
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", _FakeScheduler)

    scheduler.start_scheduler()
    scheduler.start_scheduler()

    assert len(_FakeScheduler.instances) == 1
    assert _FakeScheduler.instances[0].kwargs["job_defaults"] == {
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 300,
    }
    assert _FakeScheduler.instances[0].running is True
    assert len(_FakeScheduler.instances[0].jobs) >= 20
    job_ids = {job["id"] for job in _FakeScheduler.instances[0].jobs}
    assert "position_monitor" in job_ids
    assert "position_monitor_watchdog_5m" in job_ids

    scheduler.shutdown_scheduler()
