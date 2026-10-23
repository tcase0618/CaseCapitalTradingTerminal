import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from services import accountant_research


class _Snapshots:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query, *_args, **_kwargs):
        row = self.rows.get(query.get("ticker"))
        return dict(row) if row else None

    async def update_one(self, query, update, upsert=False):
        ticker = query["ticker"]
        row = self.rows.setdefault(ticker, {"ticker": ticker})
        row.update(update.get("$setOnInsert") or {})
        row.update(update.get("$set") or {})


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_status_is_explicitly_research_only(monkeypatch):
    monkeypatch.setenv("ACCOUNTANT_API_BASE_URL", "http://accountant.internal")
    status = accountant_research.status()

    assert status["configured"] is True
    assert status["research_only"] is True
    assert status["decision_authority"] == "NONE"
    assert status["wired_to_execution"] is False


@pytest.mark.parametrize('phase', ['cache', 'integration', 'packet', 'persist'])
def test_total_research_deadline_rejects_evidence_and_cancels_io(monkeypatch, bridge, phase):
    monkeypatch.setattr(accountant_research, 'RESEARCH_BUDGET_SECONDS', 0.02, raising=False)
    cancelled = []

    async def hanging(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(phase)

    original_get = accountant_research._get_json

    async def get_json(client, path):
        if (phase == 'integration' and '/integration/' in path) or (phase == 'packet' and '/research-packets/' in path):
            return await hanging()
        return await original_get(client, path)

    # Even a previously accepted cache row cannot authorize a timed-out request.
    bridge.store.rows['AAPL'] = {'ticker': 'AAPL', 'ok': True, 'acceptance': {'accepted': True}}
    monkeypatch.setattr(accountant_research, '_get_json', get_json)
    if phase == 'cache':
        monkeypatch.setattr(bridge.store, 'find_one', hanging)
    if phase == 'persist':
        monkeypatch.setattr(bridge.store, 'update_one', hanging)

    async def run():
        return await asyncio.wait_for(accountant_research.ticker_research('AAPL'), timeout=0.5)

    result = asyncio.run(run())
    assert result['ok'] is False
    assert result['reason'] == 'accountant_research_timeout'
    assert result['acceptance']['accepted'] is False
    assert result['research_only'] is True
    assert result['execution_allowed'] is False
    assert result['decision_authority'] == 'NONE'
    assert cancelled == [phase]


def test_enrichment_has_one_budget_for_entire_batch(monkeypatch):
    monkeypatch.setenv('ACCOUNTANT_API_BASE_URL', 'http://accountant.invalid')
    monkeypatch.setenv('ACCOUNTANT_RESEARCH_ENABLED', 'true')
    monkeypatch.setattr(accountant_research, 'RESEARCH_BUDGET_SECONDS', 0.02, raising=False)
    active = set()

    async def delayed(ticker):
        active.add(ticker)
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(ticker)

    monkeypatch.setattr(accountant_research, 'ticker_research', delayed)
    rows = [{'ticker': f'T{n}', 'pm_routable': True, 'accountant_research': {'ok': True}} for n in range(36)]

    async def run():
        result = await asyncio.wait_for(accountant_research.enrich_scan_candidates(rows), timeout=0.5)
        assert not active
        return result

    result = asyncio.run(run())
    assert result['ok'] is False
    assert result['reason'] == 'accountant_research_timeout'
    assert result['requested'] == 36
    assert result['enriched'] == 0
    assert result['unavailable'] == 36
    assert all('accountant_research' not in row for row in rows)


def test_two_http_calls_share_one_deadline(monkeypatch, bridge):
    # Leave ample timer-resolution margin on Windows while keeping each
    # individual request below the shared budget and their sum above it.
    monkeypatch.setattr(accountant_research, 'RESEARCH_BUDGET_SECONDS', 0.3)
    original_get = accountant_research._get_json
    calls = []

    async def slow_get(client, path):
        calls.append(path)
        await asyncio.sleep(0.2)
        return await original_get(client, path)

    monkeypatch.setattr(accountant_research, '_get_json', slow_get)
    result = asyncio.run(accountant_research.ticker_research('AAPL'))
    assert len(calls) == 2
    assert result['reason'] == 'accountant_research_timeout'
    assert result['acceptance']['accepted'] is False
    assert not bridge.store.rows


def test_caller_cancellation_is_not_converted_to_research_result(monkeypatch, bridge):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(bridge.store, 'find_one', cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(accountant_research.ticker_research('AAPL'))


def test_ticker_research_compacts_packet_and_never_keeps_raw_facts(monkeypatch):
    monkeypatch.setenv("ACCOUNTANT_API_BASE_URL", "http://accountant.internal")
    store = _Snapshots()
    monkeypatch.setattr(accountant_research, "get_db", lambda: SimpleNamespace(accountant_research_snapshots=store))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            if url.endswith("/api/integration/accountant/AAPL"):
                return _Response(200, {
                    "ticker": "AAPL",
                    "ready_for_readonly_integration": True,
                    "pipeline_stage": "reports-ready",
                    "report_available": True,
                    "report_card_available": True,
                    "canonical_facts_count": 1234,
                    "statement_snapshots_count": 4,
                    "stance": "WATCH",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                })
            if url.endswith("/api/research-packets/AAPL"):
                return _Response(200, {
                    **_packet(),
                    "packet_id": "packet-aapl",
                    "report": {
                        "stance": "WATCH", "composite_score": 77.5,
                        "highlights": ["Cash flow improved."], "report_markdown": "must not persist full narrative",
                    },
                    "report_card": {"filing_type": "10-Q", "filed_date": "2026-09-30", "raw_filing_sha256": "not exported"},
                })
            return _Response(404, {})

    monkeypatch.setattr(accountant_research.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(accountant_research.ticker_research("aapl"))

    assert result["ok"] is True
    assert result["research_only"] is True
    assert result["decision_authority"] == "NONE"
    assert result["report"]["composite_score"] == 77.5
    assert result["report"]["highlights"] == ["Cash flow improved."]
    assert "report_markdown" not in result["report"]
    assert "raw_filing_sha256" not in result["report_card"]
    assert store.rows["AAPL"]["provider"] == "case_capital_accountant"
    assert result["acceptance"]["status"] == "VALIDATED"
    assert result["execution_allowed"] is False
    assert result["source_integrity"]["source_grade"] == "STRONG"
    assert result["research_controls"]["qa_status"] == "PASS"
    assert result["research_controls"]["hard_vetoes"] == []
    assert result["research_controls"]["qa_checks"] == {"hard_veto_clear": True}


def test_enrichment_is_bounded_and_does_not_change_pm_fields(monkeypatch):
    monkeypatch.setenv("ACCOUNTANT_API_BASE_URL", "http://accountant.internal")
    monkeypatch.setenv("ACCOUNTANT_RESEARCH_SCAN_MAX_TICKERS", "1")

    async def fake_packet(ticker, force_refresh=False):
        return {"ok": True, "ticker": ticker, "research_only": True, "decision_authority": "NONE"}

    monkeypatch.setattr(accountant_research, "ticker_research", fake_packet)
    rows = [
        {"ticker": "LOW", "pm_routable": True, "case_score": 10, "action": "STARTER"},
        {"ticker": "HIGH", "pm_routable": True, "case_score": 90, "action": "ACCUMULATE"},
    ]
    result = asyncio.run(accountant_research.enrich_scan_candidates(rows))

    assert result["requested"] == 1
    assert result["tickers"] == ["HIGH"]
    assert "accountant_research" not in rows[0]
    assert rows[1]["accountant_research"]["decision_authority"] == "NONE"
    assert rows[1]["action"] == "ACCUMULATE"


def _packet():
    now = datetime.now(timezone.utc).isoformat()
    return {
        "packet_version": "ACCOUNTANT_RESEARCH_PACKET_V2",
        "packet_id": "aapl:10q",
        "ticker": "AAPL",
        "generated_at": now,
        "acceptance": {
            "status": "VALIDATED", "accepted": True, "reasons": [],
            "source_as_of": now, "report_as_of": now,
        },
        "research_only": True,
        "execution_allowed": False,
        "operating_mode": {"mode": "terminal_research", "terminal_handoff_allowed": True},
        "report": {"ticker": "AAPL", "updated_at": now, "data_quality_tier": "HIGH", "composite_score": 77.5},
        "report_card": {"final_verdict": {"veto_triggered": False, "veto_reason": None}},
        "source_integrity": {"source_grade": "STRONG"},
        "research_controls": {
            "version": "RESEARCH_CONTROL_PLANE_V1", "approval_state": "RESEARCH_APPROVED",
            "qa_status": "PASS", "qa_checks": {"hard_veto_clear": True},
            "qa_failures": [], "hard_vetoes": [], "execution_authority": "NONE",
        },
    }


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.setenv("ACCOUNTANT_API_BASE_URL", "http://accountant.invalid")
    monkeypatch.setenv("ACCOUNTANT_RESEARCH_ENABLED", "true")
    monkeypatch.delenv("ACCOUNTANT_RESEARCH_MAX_AGE_DAYS", raising=False)
    store = _Snapshots()
    state = SimpleNamespace(
        packet=_packet(), integration={
            "ticker": "AAPL", "generated_at": datetime.now(timezone.utc).isoformat(),
            "ready_for_readonly_integration": True, "report_available": True,
        }, integration_status=200, packet_status=200, error=None, calls=[], store=store,
    )
    monkeypatch.setattr(accountant_research, "get_db", lambda: SimpleNamespace(accountant_research_snapshots=store))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, url):
            state.calls.append(url)
            if state.error:
                raise state.error
            if "/api/integration/" in url:
                return _Response(state.integration_status, state.integration)
            assert url.endswith("/api/research-packets/AAPL")
            return _Response(state.packet_status, state.packet)

    monkeypatch.setattr(accountant_research.httpx, "AsyncClient", lambda **_kwargs: Client())
    return state


def _set_path(payload, path, value):
    parts = path.split(".")
    for part in parts[:-1]:
        payload = payload[part]
    payload[parts[-1]] = value


@pytest.mark.parametrize("path,value", [
    ("packet_version", "ACCOUNTANT_RESEARCH_PACKET_V1"), ("packet_version", None),
    ("ticker", "MSFT"), ("ticker", "aapl"), ("report.ticker", "MSFT"),
    ("acceptance", None), ("acceptance.status", "UNVALIDATED"),
    ("acceptance.status", "UNAVAILABLE"), ("acceptance.accepted", False),
    ("acceptance.accepted", 1), ("acceptance.reasons", "passed"),
    ("acceptance.source_as_of", None), ("acceptance.report_as_of", "not-a-date"),
    ("acceptance.reasons", ["source_stale"]),
    ("research_only", False), ("research_only", "true"),
    ("execution_allowed", True), ("execution_allowed", 0),
    ("operating_mode", None), ("operating_mode.terminal_handoff_allowed", False),
    ("operating_mode.terminal_handoff_allowed", "true"),
    ("source_integrity.source_grade", "INSUFFICIENT"), ("source_integrity", {}),
    ("source_integrity.source_grade", []),
    ("report.data_quality_tier", "INSUFFICIENT_DATA"),
    ("report", "malformed"), ("generated_at", "not-a-time"),
])
def test_rejects_invalid_contract(bridge, path, value):
    _set_path(bridge.packet, path, value)
    result = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert result["ok"] is False
    assert result["acceptance"]["status"] == "UNVALIDATED"
    assert result["acceptance"]["accepted"] is False
    assert result["acceptance"]["reasons"]
    assert not bridge.store.rows


def test_accepts_producer_calendar_day_asof_contract(bridge):
    today = datetime.now(timezone.utc).date().isoformat()
    bridge.packet["acceptance"]["source_as_of"] = today
    bridge.packet["acceptance"]["report_as_of"] = today
    result = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert result["ok"] is True
    assert result["acceptance"]["source_as_of"] == today


@pytest.mark.parametrize("field", ["generated_at", "acceptance.source_as_of", "acceptance.report_as_of", "report.updated_at"])
@pytest.mark.parametrize("kind", ["future", "stale", "naive", "missing"])
def test_rejects_untrustworthy_timestamps(bridge, field, kind):
    now = datetime.now(timezone.utc)
    value = {
        "future": (now + timedelta(days=1)).isoformat(),
        "stale": (now - timedelta(days=8)).isoformat(),
        "naive": now.replace(tzinfo=None).isoformat(), "missing": None,
    }[kind]
    _set_path(bridge.packet, field, value)
    result = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert result["ok"] is False
    assert not bridge.store.rows


@pytest.mark.parametrize("failure", ["401", "403", "500", "timeout", "not_ready", "missing_packet", "invalid_json_shape", "wrong_ticker"])
@pytest.mark.parametrize("force_refresh", [False, True])
def test_current_failure_never_accepts_cached_evidence(bridge, failure, force_refresh):
    good = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert good["ok"] is True
    cached = deepcopy(bridge.store.rows)
    if failure.isdigit():
        bridge.integration_status = int(failure)
    elif failure == "timeout":
        bridge.error = httpx.ReadTimeout("offline test")
    elif failure == "not_ready":
        bridge.integration["ready_for_readonly_integration"] = False
    elif failure == "missing_packet":
        bridge.packet_status = 404
    elif failure == "invalid_json_shape":
        bridge.packet = []
    else:
        bridge.integration["ticker"] = "MSFT"
    result = asyncio.run(accountant_research.ticker_research("AAPL", force_refresh=force_refresh))
    assert result["ok"] is False
    assert result["acceptance"]["accepted"] is False
    assert result["stale_diagnostics"]["ok"] is False
    assert result["stale_diagnostics"]["acceptance"]["accepted"] is False
    assert "report" not in result
    assert bridge.store.rows == cached


def test_max_age_is_configurable_and_watch_is_acceptable(bridge, monkeypatch):
    bridge.packet["source_integrity"]["source_grade"] = "WATCH"
    bridge.packet["acceptance"]["source_as_of"] = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is True
    monkeypatch.setenv("ACCOUNTANT_RESEARCH_MAX_AGE_DAYS", "2")
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False
    monkeypatch.setenv("ACCOUNTANT_RESEARCH_MAX_AGE_DAYS", "invalid")
    assert accountant_research._max_age_days() == 7


def test_missing_evidence_and_integration_data_quality_fail_closed(bridge):
    bridge.packet["report"] = None
    bridge.packet["report_card"] = None
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False
    bridge.packet = _packet()
    bridge.integration["data_quality_tier"] = "INSUFFICIENT"
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False


def test_failed_enrichment_removes_prior_evidence_without_touching_pm(bridge):
    bridge.integration["ready_for_readonly_integration"] = False
    row = {
        "ticker": "AAPL", "pm_routable": True, "case_score": 90,
        "action": "ACCUMULATE", "orders": [{"status": "unchanged"}],
    }
    expected = deepcopy(row)
    row["accountant_research"] = {"ok": True, "acceptance": {"accepted": True}}
    result = asyncio.run(accountant_research.enrich_scan_candidates([row]))
    assert result["enriched"] == 0
    assert row == expected


@pytest.mark.parametrize("field", [
    "packet_version", "ticker", "generated_at", "acceptance",
    "acceptance.status", "acceptance.accepted", "acceptance.reasons",
    "acceptance.source_as_of", "acceptance.report_as_of",
    "research_only", "execution_allowed", "operating_mode",
    "operating_mode.terminal_handoff_allowed", "source_integrity",
])
def test_missing_required_fields_are_rejected(bridge, field):
    target = bridge.packet
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    del target[parts[-1]]
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False


@pytest.mark.parametrize("section", ["operating_mode", "research_controls"])
def test_conflicting_nested_authority_is_rejected(bridge, section):
    bridge.packet[section]["execution_allowed"] = True
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False


@pytest.mark.parametrize("status_code", [401, 403, 500])
def test_packet_http_errors_reject_cached_success(bridge, status_code):
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is True
    bridge.packet_status = status_code
    result = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert result["ok"] is False
    assert result["reason"] == f"accountant_packet_http_{status_code}"


@pytest.mark.parametrize("value", [None, "true", 1, False])
def test_readiness_requires_literal_true(bridge, value):
    bridge.integration["ready_for_readonly_integration"] = value
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False
    assert len(bridge.calls) == 1


@pytest.mark.parametrize("value", [None, "2026-10-21", "broken"])
def test_integration_timestamp_is_required_and_aware(bridge, value):
    bridge.integration["generated_at"] = value
    assert asyncio.run(accountant_research.ticker_research("AAPL"))["ok"] is False


def test_qa_and_veto_diagnostics_preserve_producer_names(bridge):
    bridge.packet["research_controls"].update({
        "qa_status": "FAIL", "qa_failures": ["hard_veto_clear"],
        "hard_vetoes": ["report-card veto triggered"],
    })
    bridge.packet["report_card"]["final_verdict"] = {
        "veto_triggered": True, "veto_reason": "non-reliance disclosed",
    }
    result = asyncio.run(accountant_research.ticker_research("AAPL"))
    assert result["ok"] is True  # Acceptance is research transport, not a buy approval.
    assert result["research_controls"]["hard_vetoes"] == ["report-card veto triggered"]
    assert result["research_controls"]["qa_failures"] == ["hard_veto_clear"]
    assert result["report_card"]["final_verdict"]["veto_triggered"] is True
    assert result["execution_allowed"] is False


def test_disabled_adapter_clears_existing_candidate_evidence(bridge, monkeypatch):
    monkeypatch.setenv("ACCOUNTANT_RESEARCH_ENABLED", "false")
    rows = [{"ticker": "AAPL", "pm_routable": True, "case_score": 12, "accountant_research": {"ok": True}}]
    result = asyncio.run(accountant_research.enrich_scan_candidates(rows))
    assert result["ok"] is False
    assert rows == [{"ticker": "AAPL", "pm_routable": True, "case_score": 12}]
    assert not bridge.calls
