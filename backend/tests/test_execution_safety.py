import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from services import execution_safety


def test_stable_client_order_id_is_retry_stable():
    first = execution_safety.stable_client_order_id("manual", "AAPL", "buy", 25, "2026-08-26", prefix="tf")
    second = execution_safety.stable_client_order_id("manual", "AAPL", "buy", 25, "2026-08-26", prefix="tf")
    changed = execution_safety.stable_client_order_id("manual", "AAPL", "buy", 30, "2026-08-26", prefix="tf")

    assert first == second
    assert first != changed
    assert first.startswith("tf-")
    assert len(first) <= 48


def test_claim_execution_intent_blocks_duplicate(monkeypatch):
    inserted = {}

    class FakeCollection:
        async def insert_one(self, doc):
            if doc["_id"] in inserted:
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("dup")
            inserted[doc["_id"]] = doc

        async def find_one(self, query, projection=None):
            return inserted.get(query["_id"])

    class FakeDB:
        execution_intents = FakeCollection()

    monkeypatch.setattr(execution_safety, "get_db", lambda: FakeDB())

    async def run():
        one = await execution_safety.claim_execution_intent(
            scope="equity_pm", client_order_id="tf-abc", symbol="AAPL", side="buy"
        )
        two = await execution_safety.claim_execution_intent(
            scope="equity_pm", client_order_id="tf-abc", symbol="AAPL", side="buy"
        )
        return one, two

    first, second = asyncio.run(run())

    assert first["ok"] is True
    assert second["ok"] is False
    assert second["reason"] == "duplicate_execution_intent"
