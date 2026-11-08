import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('backup_headroom', Path(__file__).parents[2] / 'deploy/vps/backup-headroom.py')
headroom = importlib.util.module_from_spec(spec)
spec.loader.exec_module(headroom)


def test_capacity_keeps_growth_buffer_and_recovery_reserve():
    assert headroom.required_free_bytes([100, 200, 150]) == 250 + 2 * 1024 ** 3


def test_initial_backup_requires_capacity_planning():
    with pytest.raises(ValueError, match='initial backup capacity'):
        headroom.required_free_bytes([])


@pytest.mark.parametrize('sufficient', [True, False])
def test_low_space_never_writes_or_prunes_archives(tmp_path, monkeypatch, sufficient):
    archive = tmp_path / 'case-capital-test.dump'
    archive.write_bytes(b'preserve this backup')
    required = headroom.required_free_bytes([archive.stat().st_size])
    available = required if sufficient else required - 1
    monkeypatch.setattr(headroom.shutil, 'disk_usage', lambda path: SimpleNamespace(free=available))
    if sufficient:
        assert headroom.check(tmp_path) == (available, required)
    else:
        with pytest.raises(ValueError, match='Backup skipped'):
            headroom.check(tmp_path)
    assert archive.read_bytes() == b'preserve this backup'
