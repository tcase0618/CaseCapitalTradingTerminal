#!/usr/bin/env python3
"""Bounded, read-only OS telemetry. Never imports terminal/broker modules."""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def snapshot():
    fields = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        key, value = line.split(':', 1)
        if key in {'MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'}:
            fields[key + '_kb'] = int(value.split()[0])
    output = subprocess.check_output([
        'systemctl', 'show', 'case-capital-terminal', '-p', 'MainPID',
        '-p', 'MemoryCurrent', '-p', 'MemoryPeak', '-p', 'ActiveState',
    ], text=True, timeout=5)
    fields.update(dict(line.split('=', 1) for line in output.splitlines() if '=' in line))
    fields['observed_at'] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(fields, sort_keys=True))


if __name__ == '__main__':
    snapshot()
