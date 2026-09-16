import asyncio

from services import pm_policy, prediction_markets


def test_pm_policy_requires_a_real_sample_before_promotion():
    bucket = {"samples": 49, "wins": 35, "sum_return": 42.0}
    assert pm_policy._summary("ACCUMULATE", bucket)["promotion_eligible"] is False
    bucket["samples"] = 50
    assert pm_policy._summary("ACCUMULATE", bucket)["promotion_eligible"] is True


def test_prediction_market_refresh_stays_disabled_without_opt_in(monkeypatch):
    monkeypatch.delenv("PREDICTION_MARKETS_ENABLED", raising=False)
    result = asyncio.run(prediction_markets.refresh_bound_markets())
    assert result["skipped"] is True
    assert result["reason"] == "prediction_markets_disabled"


def test_prediction_market_binding_rejects_ambiguous_empty_identity():
    try:
        asyncio.run(prediction_markets.bind_market(ticker="", market_id="", question="", relation=""))
    except ValueError as exc:
        assert "required" in str(exc)
    else:
        raise AssertionError("invalid external-market binding was accepted")
