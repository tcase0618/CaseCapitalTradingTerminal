"""Server-local maintenance pulse for VPS operations.

This intentionally bypasses HTTP routes and operator-session cookies by calling
service functions directly from the server process environment. It is for SSH /
systemd maintenance only, not a public API surface.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent
load_dotenv(ROOT_DIR / ".env")

from services import data_quality, execution_gate, options_desk, scheduler  # noqa: E402


def _compact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _compact(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_compact(v) for v in value]
    return value


async def _run(enforce_hard_stop: bool = True, remediate_limit: int = 18) -> dict[str, Any]:
    position_snapshot = await scheduler.persist_live_position_snapshot(triggered_by="vps_maintenance_pulse")
    options_risk = await options_desk.monitor_open_positions(enforce_hard_stop=enforce_hard_stop)
    remediation = await data_quality.remediate(limit=remediate_limit)
    quality = await data_quality.overview(force_refresh=False, record_event=False)
    system_gate = await execution_gate.overview()
    equity_gate = await execution_gate.check(scope="equity", record=False)
    options_gate = await execution_gate.check(scope="options", record=False)
    return {
        "ok": True,
        "position_snapshot": position_snapshot,
        "options_risk": {
            "ok": options_risk.get("ok"),
            "positions_checked": options_risk.get("positions_checked"),
            "closed": options_risk.get("closed") or [],
            "errors": options_risk.get("errors") or [],
        },
        "remediation": {
            "ok": remediation.get("ok"),
            "attempted": remediation.get("attempted"),
            "fixed": remediation.get("fixed"),
            "pending": remediation.get("pending"),
        },
        "quality": {
            "decision": (quality.get("trading_gate") or {}).get("decision"),
            "score": quality.get("score"),
            "critical_score": quality.get("critical_score"),
            "scoped_blocker_counts": {
                k: len(v)
                for k, v in ((quality.get("trading_gate") or {}).get("scoped_blockers") or {}).items()
            },
        },
        "gates": {
            "system": {
                "ok": system_gate.get("ok"),
                "decision": system_gate.get("decision"),
                "blockers": system_gate.get("blockers") or [],
                "warnings": system_gate.get("warnings") or [],
            },
            "equity": {
                "ok": equity_gate.get("ok"),
                "decision": equity_gate.get("decision"),
                "blockers": equity_gate.get("blockers") or [],
                "warnings": equity_gate.get("warnings") or [],
            },
            "options": {
                "ok": options_gate.get("ok"),
                "decision": options_gate.get("decision"),
                "blockers": options_gate.get("blockers") or [],
                "warnings": options_gate.get("warnings") or [],
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Case Capital server-local maintenance pulse.")
    parser.add_argument("--no-hard-stop", action="store_true", help="Check option risk without submitting hard-stop close orders.")
    parser.add_argument("--remediate-limit", type=int, default=18)
    args = parser.parse_args()
    result = asyncio.run(_run(enforce_hard_stop=not args.no_hard_stop, remediate_limit=args.remediate_limit))
    print(json.dumps(_compact(result), indent=2, default=str))


if __name__ == "__main__":
    main()
