import pytest
from datetime import timedelta

from services import pharma, telegram_events


def test_pdufa_parser_extracts_embedded_calendar_date():
    html = """
    <table>
      <tr><th>Ticker</th><th>Drug</th><th>Indication</th><th>PDUFA Date</th></tr>
      <tr><td>$MRNA</td><td>mRNA-1083</td><td>Flu/COVID combo vaccine</td><td>FDA action date Sep 30, 2026</td></tr>
    </table>
    """

    rows = pharma._parse_pdufa_html(html, "unit_test")

    assert len(rows) == 1
    assert rows[0]["ticker"] == "MRNA"
    assert rows[0]["pdufa_date"] == "2026-09-30"
    assert rows[0]["data_quality"] == "live_calendar"
    assert rows[0]["source_count"] == 1


def test_pdufa_dedupe_marks_cross_checked_source_confidence():
    rows = [
        {
            "ticker": "MRNA",
            "drug": "mRNA-1083",
            "indication": "Flu/COVID combo vaccine",
            "pdufa_date": "2026-09-30",
            "type": "BLA",
            "source": "source_a",
            "source_list": ["source_a"],
        },
        {
            "ticker": "MRNA",
            "drug": "mRNA 1083",
            "indication": "Combo flu/COVID vaccine",
            "pdufa_date": "2026-09-30",
            "type": "PDUFA",
            "source": "source_b",
            "source_list": ["source_b"],
        },
    ]

    merged = pharma._dedupe_pdufa_rows(rows)

    assert len(merged) == 1
    assert merged[0]["source_count"] == 2
    assert merged[0]["data_quality"] == "cross_checked_calendar"
    assert merged[0]["source_confidence"] >= 90


@pytest.mark.asyncio
async def test_fda_calendar_import_blocks_seed_fallback_without_opt_in(monkeypatch):
    async def fake_fetch():
        return [{
            "ticker": "MRNA",
            "drug": "mRNA-1083",
            "indication": "Combo flu/COVID vaccine",
            "pdufa_date": "2026-09-30",
            "type": "PDUFA",
            "source": "curated_seed",
            "source_list": ["curated_seed"],
            "source_count": 0,
            "source_confidence": 25,
            "data_quality": "fallback_calendar",
        }]

    async def fake_log(*args, **kwargs):
        return None

    monkeypatch.setattr(pharma, "fetch_pdufa_calendar", fake_fetch)
    monkeypatch.setattr(pharma, "log_activity", fake_log)

    result = await pharma.import_fda_calendar(persist=False, allow_fallback=False)

    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["imported"] == 0
    assert result["reason"] == "live_sources_unavailable_fallback_not_imported"


@pytest.mark.asyncio
async def test_fda_calendar_import_accepts_live_rows_dry_run(monkeypatch):
    async def fake_fetch():
        return [{
            "ticker": "MRNA",
            "drug": "mRNA-1083",
            "indication": "Combo flu/COVID vaccine",
            "pdufa_date": "2026-09-30",
            "type": "PDUFA",
            "source": "rttnews",
            "source_list": ["rttnews"],
            "source_count": 1,
            "source_confidence": 80,
            "data_quality": "live_calendar",
        }]

    monkeypatch.setattr(pharma, "fetch_pdufa_calendar", fake_fetch)

    result = await pharma.import_fda_calendar(persist=False, allow_fallback=False)

    assert result["ok"] is True
    assert result["blocked"] is False
    assert result["count"] == 1
    assert result["quality_counts"]["live_calendar"] == 1


def test_pdufa_parser_reads_marketbeat_company_cell_ticker():
    html = """
    <table>
      <tr><th>Company</th><th>Drug</th><th>Stage</th><th>Date</th></tr>
      <tr><td>BNTX BioNTech</td><td>BNT316 oncology therapy</td><td>Phase 2</td><td>Aug 20, 2026</td></tr>
      <tr><td>MRK Merck & Co.</td><td>ENFLONSIA RSV</td><td>sBLA</td><td>Aug 21, 2026</td></tr>
    </table>
    """

    rows = pharma._parse_pdufa_html(html, "marketbeat")

    assert [row["ticker"] for row in rows] == ["BNTX", "MRK"]


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
            "title": "Moderna (MRNA) cancer vaccine phase 3 trial met primary endpoint",
            "source": "Google News",
            "url": "https://example.test/mrna",
            "bullish_terms": ["phase 3", "primary endpoint"],
            "bearish_terms": [],
            "current_price": 63.5,
        },
        {
            "ticker": "MRNA",
            "shock_score": 82,
            "direction": "BULLISH",
            "title": "Duplicate Moderna cancer vaccine phase 3 headline",
            "source": "Google News",
            "url": "https://example.test/mrna-duplicate",
            "bullish_terms": ["phase 3"],
            "bearish_terms": [],
            "current_price": 63.5,
        },
        {
            "ticker": "ZYME",
            "shock_score": 80,
            "direction": "BULLISH",
            "title": "Zymeworks (ZYME) FDA approval triggers milestone payment",
            "source": "Google News",
            "url": "https://example.test/zyme",
            "bullish_terms": ["fda approval"],
            "bearish_terms": [],
            "current_price": 27.4,
        },
    ], triggered_by="test")

    assert result["sent"] is True
    assert len(sends) == 1
    assert "PHARMA CATALYST SHOCKS" in sends[0]
    assert "$MRNA" in sends[0]
    assert "$ZYME" in sends[0]
    assert sends[0].count("$MRNA") == 1
    assert records[0]["batch_type"] == "pharma_shock_alert"
    assert records[0]["metadata"]["dedupe_keys"][0].startswith("pharma_shock:MRNA:")
    assert records[0]["metadata"]["batched_count"] == 2
    assert events[0]["scope"] == "pharma"
