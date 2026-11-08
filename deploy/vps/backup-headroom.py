"""Refuse a backup that would consume PostgreSQL's recovery headroom."""
import math
import shutil
import sys
from pathlib import Path

RECOVERY_RESERVE_BYTES = 2 * 1024 ** 3


def required_free_bytes(archive_sizes: list[int]) -> int:
    if not archive_sizes:
        raise ValueError("No prior archive size: operator must establish initial backup capacity")
    return math.ceil(max(archive_sizes) * 1.25) + RECOVERY_RESERVE_BYTES


def check(directory: Path) -> tuple[int, int]:
    archives = sorted(directory.glob('case-capital-*.dump'), key=lambda path: path.stat().st_mtime)[-3:]
    required = required_free_bytes([path.stat().st_size for path in archives])
    available = shutil.disk_usage(directory).free
    if available < required:
        raise ValueError(f"Backup skipped: free={available} required={required}; preserve DB recovery headroom. "
                         "Relocate verified backups or expand storage; do not delete database files.")
    return available, required


if __name__ == '__main__':
    try:
        free, required = check(Path(sys.argv[1]))
    except (ValueError, OSError, IndexError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
    print(f"Backup headroom OK: free={free} required={required}")
