"""Immutable, preregistered experiments; no route to production promotion."""
import hashlib
import json
from datetime import datetime, timezone

from .db import get_db
from .kronos_contract import parse_time


def validate_registration(payload: dict, now: datetime) -> dict:
    required = ("experiment_id", "hypothesis", "strategy_id", "train_end", "holdout_start", "holdout_end", "metric", "config")
    if any(payload.get(key) in (None, "") for key in required):
        raise ValueError("experiment identity, hypothesis, dates, metric and config are required")
    train, start, end = (parse_time(payload.get(key)) for key in ("train_end", "holdout_start", "holdout_end"))
    if not train or not start or not end or not train < start < end:
        raise ValueError("require train_end < holdout_start < holdout_end")
    if start <= now:
        raise ValueError("preregister before the holdout starts; retrospective trials are not preregistered")
    if not isinstance(payload["config"], dict):
        raise ValueError("config must be an explicit object")
    clean = {key: payload[key] for key in required}
    clean["config_hash"] = hashlib.sha256(json.dumps(payload["config"], sort_keys=True, allow_nan=False).encode()).hexdigest()
    clean.update(registered_at=now.isoformat(), research_only=True, live_promotion_allowed=False,
                 state="PREREGISTERED", version="research_trials_v1")
    return clean


async def register(payload: dict) -> dict:
    row = validate_registration(payload, datetime.now(timezone.utc))
    db = get_db()
    existing = await db.research_experiments.find_one({"experiment_id": row["experiment_id"]}, {"_id": 0})
    if existing:
        if any(existing.get(key) != row.get(key) for key in ("hypothesis", "strategy_id", "train_end", "holdout_start", "holdout_end", "metric", "config_hash")):
            raise ValueError("registered experiment is immutable; a change requires a new experiment ID")
        return existing
    await db.research_experiments.update_one({"experiment_id": row["experiment_id"]}, {"$setOnInsert": row}, upsert=True)
    stored = await db.research_experiments.find_one({"experiment_id": row["experiment_id"]}, {"_id": 0})
    if stored.get("config_hash") != row["config_hash"]:
        raise ValueError("concurrent registration conflict")
    return stored


async def latest() -> dict:
    rows = await get_db().research_experiments.find({}, {"_id": 0}).sort("registered_at", -1).to_list(200)
    return {"research_only": True, "decision_authority": "NONE", "experiments": rows,
            "promotion": "MANUAL_REVIEW_ONLY", "multiple_testing": "All trial IDs must be retained; significance is not computed without sufficient evidence."}
