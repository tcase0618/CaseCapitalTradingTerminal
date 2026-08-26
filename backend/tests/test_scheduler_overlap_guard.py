from services import scheduler


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

    scheduler.shutdown_scheduler()
