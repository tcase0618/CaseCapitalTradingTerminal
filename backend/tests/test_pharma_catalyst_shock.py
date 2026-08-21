import pytest
from datetime import timedelta

from services import pharma, telegram_events


@pytest.mark.asyncio
async def test_pharma_option_snapshot_captures_contract(monkeypatch):
    async def fake_chain(ticker, catalyst_date=None):
        return {
            "price": 100.0,
            "expiration": "2026-10-16",
            "iv_rank": 22,
            "iv_label": "CHEAP",
            "atm_iv": 0.45,
            "data_provider": "TEST",
            "data_quality": "EXECUTION_GRADE",
        }

    def fake_contract(chain, direction, budget=500.0):
        return {
            "symbol": "MRNA261016C00105000",
            "contractSymbol": "MRNA261016C00105000",
            "strike": 105.0,
            "expiration": "2026-10-16",
            "premium": 2.5,
            "bid": 2.4,
            "ask": 2.6,
            "iv": 0.45,
            "delta": 0.54,
            "spread": 0.2,
            "liquidity": "GOOD",
            "max_loss": 250.0,
            "contracts_at_budget": 2,
        }

    monkeypatch.setattr(pharma.options_engine, "get_options_data", fake_chain)
    monkeypatch.setattr(pharma.options_engine, "find_best_contract", fake_contract)

    snap = await pharma.build_option_snapshot({
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "pdufa_date": "2026-09-30",
        "binary_event_score": 73,
        "current_price": 100.0,
    }, persist=False)

    assert snap["ok"] is True
    assert snap["status"] == "CONTRACT_SNAPSHOT"
    assert snap["authority"] == "RESEARCH_ONLY_NO_EXECUTION"
    assert snap["contract"]["symbol"] == "MRNA261016C00105000"


def test_pharma_alert_requires_contract_snapshot_for_option_claim():
    text = pharma.format_pharma_alert({
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "indication": "Combo flu/COVID vaccine",
        "pdufa_date": "2026-09-30",
        "days_until": 41,
        "binary_event_score": 73,
        "tier": "WATCH",
        "current_price": 100.0,
        "iv_rank": 20,
        "prevalence": {"pct": 0.1, "patient_count": 333000},
        "score_components": {
            "phase3": {"points": 25},
            "insider": {"points": 14},
            "iv": {"points": 15},
        },
    })

    assert "OPTIONS SNAPSHOT - NO VALIDATED CONTRACT" in text
    assert "LONG_CALL" not in text


def test_pharma_data_gate_blocks_missing_required_trade_facts():
    future = (pharma._now().date() + timedelta(days=30)).isoformat()
    gate = pharma.pharma_data_gate({
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "pdufa_date": future,
        "binary_event_score": 73,
        "current_price": None,
        "evaluated_at": pharma._now().isoformat(),
    })

    assert gate["decision"] == "BLOCK"
    assert "missing_live_price" in gate["blockers"]
    assert "clinical_trials" in gate["neutralized_exhibits"]


def test_pharma_hydrated_row_exposes_pm_options_strategy_and_scenario():
    future = (pharma._now().date() + timedelta(days=30)).isoformat()
    row = pharma._hydrate_pharma_row({
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "indication": "Combo flu/COVID vaccine",
        "pdufa_date": future,
        "days_until": 30,
        "binary_event_score": 84,
        "current_price": 100.0,
        "evaluated_at": pharma._now().isoformat(),
        "trial": {"nct_id": "NCT123", "status": "COMPLETED"},
        "iv_rank": 22,
        "option_snapshot": {
            "ok": True,
            "status": "CONTRACT_SNAPSHOT",
            "contract": {"symbol": "MRNA261016C00105000", "expiration": "2026-10-16", "strike": 105, "premium": 2.5},
        },
        "pm_decision": {
            "authority": "PM_DISCRETION_NO_PHARMA_EXECUTION",
            "decision": {"action": "WATCH", "pm_score": 71.5, "risk_reward": 1.7},
        },
    })

    assert row["data_gate"]["decision"] in {"PASS", "WATCH"}
    assert row["pm_summary"]["action"] == "WATCH"
    assert row["option_summary"]["contract"] == "MRNA261016C00105000"
    assert row["strategy_read"]["strategy"] == "LONG_CALL_RESEARCH_CANDIDATE"
    assert row["scenario"]["approval_probability_proxy"] > 30


def test_pharma_pm_candidate_routes_like_scan_row():
    row = {
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "indication": "Combo flu/COVID vaccine",
        "pdufa_date": "2026-09-30",
        "days_until": 41,
        "binary_event_score": 73,
        "tier": "WATCH",
        "current_price": 100.0,
        "iv_rank": 20,
        "prevalence": {"pct": 0.1, "patient_count": 333000},
        "score_components": {
            "phase3": {"points": 25},
            "insider": {"points": 14},
            "iv": {"points": 15},
        },
        "option_snapshot": {
            "ok": True,
            "chain": {
                "iv_rank": 20,
                "iv_label": "CHEAP",
                "data_provider": "ALPACA_OPTIONS",
                "data_quality": "EXECUTION_GRADE",
                "spot": 100.0,
            },
            "contract": {
                "symbol": "MRNA261016C00105000",
                "strike": 105.0,
                "expiration": "2026-10-16",
                "premium": 2.5,
                "bid": 2.4,
                "ask": 2.6,
                "iv": 0.45,
                "delta": 0.54,
                "max_loss": 250.0,
                "liquidity": "GOOD",
            },
        },
    }

    candidate = pharma.build_pm_candidate(row)

    assert candidate["source_type"] == "PHARMA_PDUFA"
    assert "PHARMA_PDUFA" in candidate["signals"]
    assert candidate["options"]["strategy"] == "LONG_CALL"
    assert candidate["target_blended"] == 140.0
    assert candidate["stop_loss"] == 85.0


@pytest.mark.asyncio
async def test_pharma_route_to_pm_returns_discretionary_ruling():
    docket = await pharma.route_to_pm({
        "ticker": "MRNA",
        "drug": "mRNA-1083",
        "pdufa_date": "2026-09-30",
        "days_until": 41,
        "binary_event_score": 73,
        "tier": "WATCH",
        "current_price": 100.0,
        "iv_rank": 20,
        "score_components": {
            "phase3": {"points": 25},
            "insider": {"points": 14},
            "iv": {"points": 15},
        },
        "option_snapshot": {
            "ok": False,
            "status": "NO_CHAIN",
            "reason": "test",
        },
    }, persist=False)

    assert docket["authority"] == "PM_DISCRETION_NO_PHARMA_EXECUTION"
    assert docket["decision"]["ticker"] == "MRNA"
    assert docket["decision"]["action"] in {"ACCUMULATE", "STARTER", "WATCH", "REJECT"}


def test_pharma_shock_maps_company_alias_to_ticker():
    article = {
        "title": "Moderna cancer vaccine phase 3 trial results send shares higher",
        "summary": "",
        "tickers": [],
    }

    assert "MRNA" in pharma._map_pharma_article_tickers(article)


def test_pharma_shock_scores_bullish_clinical_breakout():
    article = {
        "title": "Moderna cancer vaccine phase 3 trial met primary endpoint",
        "summary": "Shares surged after positive data from a late-stage study.",
        "tickers": [],
        "score": 85,
        "age_minutes": 42,
    }

    row = pharma._score_catalyst_shock(article, "MRNA", 63.5)

    assert row["direction"] == "BULLISH"
    assert row["tier"] in {"WATCH", "BREAKOUT"}
    assert row["shock_score"] >= pharma.CATALYST_SHOCK_THRESHOLD
    assert "phase 3" in row["bullish_terms"]
    assert "primary endpoint" in row["bullish_terms"]


def test_pharma_shock_scores_bearish_trial_failure():
    article = {
        "title": "Biotech trial failed to meet primary endpoint",
        "summary": "Company says FDA response letter may delay approval.",
        "tickers": ["ABCD"],
        "score": 78,
        "age_minutes": 30,
    }

    row = pharma._score_catalyst_shock(article, "ABCD", 12.0)

    assert row["direction"] == "BEARISH"
    assert "failed to meet" in row["bearish_terms"]
    assert "response letter" in row["bearish_terms"]


class _NoRecentDeliveries:
    async def find_one(self, *_args, **_kwargs):
        return None


class _Db:
    def __init__(self):
        self.telegram_deliveries = _NoRecentDeliveries()


@pytest.mark.asyncio
async def test_pharma_shock_dispatch_formats_and_records(monkeypatch):
    sends = []
    records = []
    events = []

    async def fake_send(text):
        sends.append(text)
        return True

    async def fake_record(batch_type, title, text, event_rows, sent, metadata=None):
        records.append({
            "batch_type": batch_type,
            "title": title,
            "text": text,
            "event_rows": event_rows,
            "sent": sent,
            "metadata": metadata or {},
        })

    async def fake_emit(*args, **kwargs):
        event = {"fingerprint": "fp", "severity": kwargs.get("severity"), **kwargs}
        events.append(event)
        return event

    monkeypatch.setattr(telegram_events, "get_db", lambda: _Db())
    monkeypatch.setattr(telegram_events, "_send", fake_send)
    monkeypatch.setattr(telegram_events, "_record_delivery", fake_record)
    monkeypatch.setattr(telegram_events, "emit_event", fake_emit)

    result = await telegram_events.dispatch_pharma_shock_alerts([
        {
            "ticker": "MRNA",
            "shock_score": 91,
            "direction": "BULLISH",
            "title": "Moderna cancer vaccine phase 3 trial met primary endpoint",
            "source": "Google News",
            "url": "https://example.test/mrna",
            "bullish_terms": ["phase 3", "primary endpoint"],
            "bearish_terms": [],
            "current_price": 63.5,
        }
    ], triggered_by="test")

    assert result["sent"] is True
    assert sends and "PHARMA CATALYST SHOCK" in sends[0]
    assert "$MRNA" in sends[0]
    assert records[0]["batch_type"] == "pharma_shock_alert"
    assert records[0]["metadata"]["dedupe_key"].startswith("pharma_shock:MRNA:")
    assert events[0]["scope"] == "pharma"
