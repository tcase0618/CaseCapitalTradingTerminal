"""Read-only prediction-market evidence for the PM dossier.

Bindings are explicit: a ticker is never matched to a market merely because a
search result contains similar text.  This protects the PM from attaching an
unrelated binary event to a public-company decision.  No function here imports
or calls an execution adapter.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_db, stamped

GAMMA_URL = os.environ.get("PREDICTION_MARKETS_GAMMA_URL", "https://gamma-api.polymarket.com").rstrip("/")
CLOB_URL = os.environ.get("PREDICTION_MARKETS_CLOB_URL", "https://clob.polymarket.com").rstrip("/")


def enabled() -> bool:
    return os.environ.get("PREDICTION_MARKETS_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _ticker(value: Any) -> str:
    return str(value or "").upper().strip()


async def bind_market(*, ticker: str, market_id: str, question: str, relation: str, token_id: str | None = None, mapping_confidence: float = 0.0, verified_by: str = "operator") -> dict[str, Any]:
    """Store a manually verified, research-only relationship."""
    symbol = _ticker(ticker)
    if not symbol or not str(market_id).strip() or not str(question).strip() or not str(relation).strip():
        raise ValueError("ticker, market_id, question, and relation are required")
    binding_id = f"prediction-market:{symbol}:{market_id}"
    doc = stamped({
        "binding_id": binding_id,
        "ticker": symbol,
        "market_id": str(market_id),
        "token_id": str(token_id or "").strip() or None,
        "question": str(question).strip()[:1000],
        "relation": str(relation).strip()[:240],
        "mapping_confidence": max(0.0, min(float(mapping_confidence), 1.0)),
        "verified_by": verified_by,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "execution_role": "RESEARCH_ONLY",
        "automatic_order_authority": False,
    })
    db = get_db()
    await db.pm_prediction_market_bindings.update_one({"binding_id": binding_id}, {"$set": doc}, upsert=True)
    return doc


async def _fetch_market(client: httpx.AsyncClient, market_id: str, token_id: str | None) -> dict[str, Any]:
    response = await client.get(f"{GAMMA_URL}/markets/{market_id}")
    response.raise_for_status()
    market = response.json()
    result: dict[str, Any] = {"market": market}
    if token_id:
        quote = await client.get(f"{CLOB_URL}/price", params={"token_id": token_id, "side": "BUY"})
        quote.raise_for_status()
        result["quote"] = quote.json()
    return result


async def refresh_bound_markets(limit: int = 100) -> dict[str, Any]:
    """Fetch only already-bound markets. Failure never impacts PM execution."""
    if not enabled():
        return {"ok": True, "skipped": True, "reason": "prediction_markets_disabled"}
    db = get_db()
    bindings = await db.pm_prediction_market_bindings.find({}, {"_id": 0}).to_list(max(1, min(limit, 500)))
    refreshed = errors = 0
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": "CaseCapitalResearch/1.0"}) as client:
        for binding in bindings:
            try:
                raw = await _fetch_market(client, str(binding["market_id"]), binding.get("token_id"))
                market = raw.get("market") or {}
                quote = raw.get("quote") or {}
                observation = stamped({
                    "observation_id": f"prediction-market-observation:{binding['binding_id']}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}",
                    "binding_id": binding["binding_id"],
                    "ticker": binding["ticker"],
                    "market_id": binding["market_id"],
                    "question": binding["question"],
                    "relation": binding["relation"],
                    "market_active": market.get("active"),
                    "market_closed": market.get("closed"),
                    "end_date": market.get("endDate") or market.get("end_date_iso"),
                    "liquidity": market.get("liquidity"),
                    "volume": market.get("volume"),
                    "outcome_price": quote.get("price") if isinstance(quote, dict) else None,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    "execution_role": "RESEARCH_ONLY",
                })
                await db.pm_prediction_market_observations.update_one({"observation_id": observation["observation_id"]}, {"$set": observation}, upsert=True)
                refreshed += 1
            except Exception as exc:
                errors += 1
                await db.pm_prediction_market_bindings.update_one({"binding_id": binding.get("binding_id")}, {"$set": {"last_refresh_error": exc.__class__.__name__, "last_refresh_attempt_at": datetime.now(timezone.utc).isoformat()}})
    return {"ok": errors == 0, "read_only": True, "refreshed": refreshed, "errors": errors}


async def ticker_evidence(ticker: str, limit: int = 20) -> list[dict[str, Any]]:
    symbol = _ticker(ticker)
    bindings = await get_db().pm_prediction_market_bindings.find({"ticker": symbol}, {"_id": 0}).to_list(max(1, min(limit, 100)))
    rows: list[dict[str, Any]] = []
    for binding in bindings:
        latest = await get_db().pm_prediction_market_observations.find_one({"binding_id": binding.get("binding_id")}, {"_id": 0}, sort=[("observed_at", -1)]) or None
        rows.append({"binding": binding, "latest_observation": latest})
    return rows
