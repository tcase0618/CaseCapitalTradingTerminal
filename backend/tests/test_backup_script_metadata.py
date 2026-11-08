"""Linux shell harness: fake dump/restore executables, private temporary files."""
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from services import backup_health


@pytest.mark.skipif(shutil.which('bash') is None, reason='bash unavailable; Linux CI runs the isolated shell harness')
@pytest.mark.parametrize('failure,expected', [('', 0), ('dump', 7), ('restore', 8)])
def test_only_completed_script_run_advances_success_record(tmp_path, failure, expected):
    root = Path(__file__).resolve().parents[2]
    backups = tmp_path / 'backups'
    backups.mkdir()
    prior = backups / 'case-capital-prior.dump'
    prior.write_bytes(b'prior offline dump')
    Path(str(prior) + '.sha256').write_text(hashlib.sha256(prior.read_bytes()).hexdigest() + '  ' + prior.name)
    backup_health.record_success(prior)
    marker = backups / 'last-success.json'
    original = marker.read_bytes()
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    stubs = {
        'python3': '#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n',
        'nice': '#!/bin/sh\nshift 2\nexec "$@"\n',
        'ionice': '#!/bin/sh\nshift 2\nexec "$@"\n',
        'pg_dump': '#!/bin/sh\n[ "$OFFLINE_FAILURE" = dump ] && exit 7\nfor arg do\ncase "$arg" in --file=*) printf offline_fixture > "${arg#--file=}";; esac\ndone\n',
        'pg_restore': '#!/bin/sh\n[ "$OFFLINE_FAILURE" = restore ] && exit 8\nexit 0\n',
    }
    for name, content in stubs.items():
        path = binaries / name
        path.write_text(content)
        path.chmod(0o700)
    env_file = tmp_path / 'offline.env'
    env_file.write_text('POSTGRES_DSN=postgresql://offline:dummy@127.0.0.1/isolated\n')
    env = {**os.environ, 'PATH': str(binaries) + os.pathsep + os.environ['PATH'],
           'APP_DIR': str(root), 'ENV_FILE': str(env_file), 'CASE_CAPITAL_BACKUP_DIR': str(backups),
           'OFFLINE_FAILURE': failure}
    result = subprocess.run([shutil.which('bash'), str(root / 'deploy/vps/backup-postgres.sh')],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == expected, result.stderr
    if failure:
        assert marker.read_bytes() == original
    else:
        assert marker.read_bytes() != original
        assert backup_health.status(directory=backups)['status'] == 'RECENT'
    assert prior.read_bytes() == b'prior offline dump'
