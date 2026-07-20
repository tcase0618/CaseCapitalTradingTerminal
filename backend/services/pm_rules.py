"""PM ruleset versioning.

Rulesets are lightweight overlays on top of portfolio_manager.MODE_PROFILES.
Only changed fields are stored. The PM evaluator still owns the actual logic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import portfolio_manager
from .db import get_db, stamped

ACTIVE_DOC_ID = "active"
DEFAULT_RULESET_ID = "pm-default-v1"

ALLOWED_FIELDS = {
    "max_position_pct",
    "max_single_name_risk_pct",
    "max_gross_deployment_pct",
    "accumulate_score",
    "accumulate_rr",
    "starter_score",
    "starter_rr",
    "watch_score",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_profile(profile: dict[str, Any] | None) -> dict[str, float]:
    cleaned: dict[str, float] = {}
    for key, value in (profile or {}).items():
        if key not in ALLOWED_FIELDS:
            continue
        try:
            cleaned[key] = float(value)
        except (TypeError, ValueError):
            continue
    return cleaned


def default_ruleset() -> dict[str, Any]:
    return {
        "ruleset_id": DEFAULT_RULESET_ID,
        "name": "PM Default v1",
        "description": "Built-in Case Cap PM rules.",
        "active": True,
        "mode_overrides": {},
        "base_profiles": portfolio_manager.MODE_PROFILES,
        "created_at": None,
        "updated_at": None,
    }


async def ensure_default() -> None:
    db = get_db()
    existing = await db.pm_rulesets.find_one({"ruleset_id": DEFAULT_RULESET_ID})
    if not existing:
        await db.pm_rulesets.insert_one(stamped(default_ruleset()))
    active = await db.pm_rulesets_state.find_one({"_id": ACTIVE_DOC_ID})
    if not active:
        await db.pm_rulesets_state.insert_one({
            "_id": ACTIVE_DOC_ID,
            "ruleset_id": DEFAULT_RULESET_ID,
            "updated_at": _now(),
        })


async def list_rulesets() -> dict[str, Any]:
    await ensure_default()
    db = get_db()
    active = await active_ruleset_id()
    docs = await db.pm_rulesets.find({}, {"_id": 0}).sort("created_at", -1).to_list(100)
    for doc in docs:
        doc["active"] = doc.get("ruleset_id") == active
    return {"active_ruleset_id": active, "rulesets": docs, "allowed_fields": sorted(ALLOWED_FIELDS)}


async def active_ruleset_id() -> str:
    db = get_db()
    state = await db.pm_rulesets_state.find_one({"_id": ACTIVE_DOC_ID}, {"_id": 0})
    return (state or {}).get("ruleset_id") or DEFAULT_RULESET_ID


async def get_ruleset(ruleset_id: str | None = None) -> dict[str, Any]:
    await ensure_default()
    db = get_db()
    rid = ruleset_id or await active_ruleset_id()
    doc = await db.pm_rulesets.find_one({"ruleset_id": rid}, {"_id": 0})
    if not doc:
        doc = default_ruleset()
    doc["active"] = doc.get("ruleset_id") == await active_ruleset_id()
    return doc


async def create_ruleset(
    name: str,
    description: str = "",
    mode_overrides: dict[str, Any] | None = None,
    activate: bool = False,
) -> dict[str, Any]:
    await ensure_default()
    db = get_db()
    safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-") or "custom"
    ruleset_id = f"pm-{safe_name}-{int(datetime.now(timezone.utc).timestamp())}"
    cleaned_modes = {}
    for mode, profile in (mode_overrides or {}).items():
        m = str(mode).upper()
        if m in portfolio_manager.MODE_PROFILES:
            cleaned_modes[m] = _clean_profile(profile)
    doc = stamped({
        "ruleset_id": ruleset_id,
        "name": name,
        "description": description,
        "active": False,
        "mode_overrides": cleaned_modes,
        "base_profiles": portfolio_manager.MODE_PROFILES,
        "updated_at": _now(),
    })
    await db.pm_rulesets.insert_one(doc)
    if activate:
        await activate_ruleset(ruleset_id)
        doc["active"] = True
    doc.pop("_id", None)
    return doc


async def activate_ruleset(ruleset_id: str) -> dict[str, Any]:
    await ensure_default()
    db = get_db()
    doc = await db.pm_rulesets.find_one({"ruleset_id": ruleset_id}, {"_id": 0})
    if not doc:
        return {"ok": False, "reason": "ruleset_not_found", "ruleset_id": ruleset_id}
    await db.pm_rulesets_state.update_one(
        {"_id": ACTIVE_DOC_ID},
        {"$set": {"ruleset_id": ruleset_id, "updated_at": _now()}},
        upsert=True,
    )
    return {"ok": True, "active_ruleset_id": ruleset_id}


async def profile_override_for(mode: str, ruleset_id: str | None = None) -> dict[str, Any]:
    doc = await get_ruleset(ruleset_id)
    return ((doc.get("mode_overrides") or {}).get((mode or "BALANCED").upper()) or {})
