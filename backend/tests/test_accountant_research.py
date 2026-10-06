import asyncio
from types import SimpleNamespace

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
                    "ready_for_readonly_integration": True,
                    "pipeline_stage": "reports-ready",
                    "report_available": True,
                    "report_card_available": True,
                    "canonical_facts_count": 1234,
                    "statement_snapshots_count": 4,
                    "stance": "WATCH",
                    "generated_at": "2026-10-06T00:00:00Z",
                })
            if url.endswith("/api/research-packets/AAPL"):
                return _Response(200, {
                    "packet_version": "ACCOUNTANT_RESEARCH_PACKET_V1",
                    "packet_id": "packet-aapl",
                    "generated_at": "2026-10-06T00:00:00Z",
                    "report": {
                        "stance": "WATCH", "composite_score": 77.5,
                        "highlights": ["Cash flow improved."], "report_markdown": "must not persist full narrative",
                    },
                    "report_card": {"filing_type": "10-Q", "filed_date": "2026-09-30", "raw_filing_sha256": "not exported"},
                    "research_controls": {"research_only": True, "execution_allowed": False},
                    "source_integrity": {"ok": True, "quality": "high"},
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
