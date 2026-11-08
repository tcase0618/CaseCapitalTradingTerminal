"""Local metadata fixtures only; no pg_dump, database, broker or network."""
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from services import backup_health


@pytest.fixture
def completed(tmp_path):
    archive = tmp_path / 'case-capital-offline.dump'
    archive.write_bytes(b'explicit offline fixture')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    (tmp_path / (archive.name + '.sha256')).write_text(checksum + '  ' + archive.name)
    backup_health.record_success(archive)
    return archive, tmp_path / 'last-success.json'


@pytest.mark.parametrize('hours,expected', [(1, 'RECENT'), (26, 'RECENT'), (27, 'STALE')])
def test_backup_age_and_size_are_from_completed_dump(completed, hours, expected):
    archive, marker = completed
    record = json.loads(marker.read_text())
    completed_at = datetime.fromisoformat(record['completed_at'])
    result = backup_health.status(directory=archive.parent, now=completed_at + timedelta(hours=hours))
    assert result['status'] == expected
    assert result['ok'] == (expected == 'RECENT')
    assert result['size_bytes'] == archive.stat().st_size and result['age_hours'] == hours
    assert result['restore_status'] == result['off_host_status'] == 'UNVERIFIED'


@pytest.mark.parametrize('case', ['missing-archive', 'changed-size', 'changed-checksum', 'future',
                                 'naive', 'path-traversal', 'malformed', 'oversized'])
def test_unverifiable_completion_does_not_claim_a_recent_backup(completed, case):
    archive, marker = completed
    record = json.loads(marker.read_text())
    if case == 'missing-archive':
        archive.unlink()
    elif case == 'changed-size':
        archive.write_bytes(b'changed')
    elif case == 'changed-checksum':
        archive.with_suffix('.dump.sha256').write_text('b' * 64 + '  ' + archive.name)
    elif case == 'future':
        record['completed_at'] = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    elif case == 'naive':
        record['completed_at'] = '2026-11-08T01:00:00'
    elif case == 'path-traversal':
        record['archive'] = '../' + archive.name
    elif case == 'malformed':
        marker.write_text('bad-json')
    else:
        marker.write_text('x' * 65537)
    if case in {'future', 'naive', 'path-traversal'}:
        marker.write_text(json.dumps(record))
    result = backup_health.status(directory=archive.parent)
    assert not result['ok'] and result['status'] == 'UNVERIFIED'
    assert result['age_hours'] is None and result['size_bytes'] is None


def test_missing_completion_does_not_invent_backup_age(tmp_path):
    result = backup_health.status(directory=tmp_path)
    assert not result['ok'] and result['status'] == 'MISSING' and result['age_hours'] is None


def test_failed_completion_write_preserves_previous_success(completed, monkeypatch):
    archive, marker = completed
    original = marker.read_bytes()
    monkeypatch.setattr(backup_health.os, 'replace', lambda *args: (_ for _ in ()).throw(OSError('offline failure')))
    with pytest.raises(OSError):
        backup_health.record_success(archive)
    assert marker.read_bytes() == original
    assert not list(archive.parent.glob('.last-success-*'))


def test_incomplete_dump_cannot_replace_success_record(completed):
    archive, marker = completed
    previous = marker.read_bytes()
    archive.with_suffix('.dump.sha256').unlink()
    with pytest.raises((OSError, ValueError)):
        backup_health.record_success(archive)
    assert marker.read_bytes() == previous
