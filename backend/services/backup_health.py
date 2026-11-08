"""Local dump-completion metadata; not an off-host or restore verification."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile
from datetime import datetime, timezone


def _checksum(archive: Path) -> str:
    marker = Path(str(archive) + ".sha256")
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 65536:
        raise ValueError("invalid checksum marker")
    digest, name = marker.read_text().strip().split(maxsplit=1)
    if not re.fullmatch(r"[a-fA-F0-9]{64}", digest) or Path(name.lstrip("*")).name != archive.name:
        raise ValueError("invalid checksum marker")
    return digest.lower()


def record_success(archive: Path) -> None:
    archive = Path(archive)
    if (not re.fullmatch(r"case-capital-[A-Za-z0-9T_-]+\.dump", archive.name)
            or archive.is_symlink() or not archive.is_file() or archive.stat().st_size <= 0):
        raise ValueError("invalid backup archive")
    record = {"archive": archive.name, "size_bytes": archive.stat().st_size,
              "sha256": _checksum(archive), "completed_at": datetime.now(timezone.utc).isoformat()}
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=archive.parent,
                                         prefix=".last-success-", delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, archive.parent / "last-success.json")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def status(*, directory: Path | None = None, now: datetime | None = None) -> dict:
    result = {"ok": False, "status": "MISSING", "last_success_at": None,
              "age_hours": None, "size_bytes": None,
              "verification": "completion_record_and_file_metadata_only",
              "restore_status": "UNVERIFIED", "off_host_status": "UNVERIFIED"}
    root = Path(directory or os.environ.get("CASE_CAPITAL_BACKUP_DIR", "/opt/case-capital/backups/postgres"))
    marker = root / "last-success.json"
    try:
        if not marker.exists():
            return result
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 65536:
            raise ValueError("invalid completion record")
        record = json.loads(marker.read_text(encoding="utf-8"))
        name = record["archive"]
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("invalid archive name")
        archive = root / name
        if (not re.fullmatch(r"case-capital-[A-Za-z0-9T_-]+\.dump", name)
                or archive.is_symlink() or not archive.is_file()):
            raise ValueError("invalid archive")
        size = record["size_bytes"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0 or archive.stat().st_size != size:
            raise ValueError("archive metadata changed")
        if _checksum(archive) != record["sha256"]:
            raise ValueError("checksum marker changed")
        completed = datetime.fromisoformat(record["completed_at"].replace("Z", "+00:00"))
        if not completed.tzinfo:
            raise ValueError("timezone missing")
        age = ((now or datetime.now(timezone.utc)) - completed).total_seconds()
        if age < 0:
            raise ValueError("future completion")
        result.update(ok=age <= 26 * 3600, status="RECENT" if age <= 26 * 3600 else "STALE",
                      last_success_at=completed.isoformat(), age_hours=round(age / 3600, 2), size_bytes=size)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        result["status"] = "UNVERIFIED"
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Record successful local PostgreSQL dump completion")
    parser.add_argument("archive", type=Path)
    record_success(parser.parse_args().archive)
