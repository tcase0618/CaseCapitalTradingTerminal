from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import portfolio_manager, public_execution, safety, telegram_events
from services import trade_floor_learning


@pytest.mark.asyncio
@pytest.mark.parametrize("session,broker_quantity,pending,expected", [
    ("CORE", 1.67597, False, 1.67597),
    ("TWENTY_FOUR_HOURS", 1.67597, False, 1.0),
    ("TWENTY_FOUR_HOURS", 0.67597, False, None),
    ("CORE", 1.67597, True, None),
])
async def test_automatic_public_stop_exit_uses_broker_quantity_and_session(monkeypatch, session, broker_quantity, pending, expected):
    trade = {"ticker": "TEST", "client_order_id": "entry-test", "qty_remaining": 3.0,
             "current_stop": 3.0, "emergency_exit_order_id": "pending-sell" if pending else None}
    updates = []
    submissions = []

    class Cursor:
        async def to_list(self, _limit):
            return [trade]

    class Trades:
        def find(self, *_args):
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def portfolio(self):
            return {"positions": [{"symbol": "TEST", "quantity": broker_quantity}]}

        async def quotes(self, _symbols):
            return {"quotes": [{"symbol": "TEST", "bid": 2.67, "ask": 2.69,
                                "quoteTime": datetime.now(timezone.utc).isoformat()}]}

        async def submit_equity_order(self, **kwargs):
            submissions.append(kwargs)
            return {"order": {"orderId": "stop-exit"}}

    async def claim(**_kwargs):
        return {"ok": True}

    async def marked(*_args):
        pass

    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: Client())
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: session)
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", claim)
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", marked)

    result = await public_execution.process_protective_exits()

    assert result["ok"] is True
    if expected is None:
        assert submissions == []
    else:
        assert submissions[0]["quantity"] == expected
        assert submissions[0]["session"] == session
        assert submissions[0]["limit_price"] == 2.67
        assert updates[0]["emergency_exit_order_qty"] == expected


def test_public_trade_quantity_prefers_reconciled_remaining_quantity():
    assert public_execution._qty({"qty_remaining": 1.25, "quantity": 0.0}) == 1.25


def test_public_trade_quantity_supports_portfolio_quantity():
    assert public_execution._qty({"quantity": "2.5"}) == 2.5


def test_public_core_rows_keep_a_durable_strategy_identity():
    attribution = public_execution._strategy_attribution({"ticker": "AAPL"})
    assert attribution["strategy_id"] == "CORE"
    assert attribution["screener_id"] == "CORE"
    assert attribution["scanner_family"] == "CORE"


def test_execution_quote_requires_two_sided_market_and_respects_spread(monkeypatch):
    missing, reason = public_execution._execution_quote({"ask": 10.0}, side="BUY")
    assert missing is None
    assert reason == "public_execution_quote_requires_valid_bid_ask"

    monkeypatch.setenv("PUBLIC_MAX_EQUITY_SPREAD_BPS", "100")
    wide, reason = public_execution._execution_quote({"bid": 9.0, "ask": 10.0}, side="BUY")
    assert wide is None
    assert reason == "public_execution_quote_spread_too_wide"

    buy, _ = public_execution._execution_quote({"bid": 9.98, "ask": 10.02}, side="BUY")
    emergency_sell, _ = public_execution._execution_quote({"bid": 9.98, "ask": 10.02}, side="SELL", emergency=True)
    assert buy["limit_price"] == 10.0
    assert emergency_sell["limit_price"] == 9.98


@pytest.mark.asyncio
async def test_exit_quote_refresh_retries_until_a_fresh_executable_quote(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = 0

        async def quotes(self, symbols):
            assert symbols == ["AAPL"]
            self.calls += 1
            timestamp = (
                datetime.now(timezone.utc) - timedelta(seconds=300)
                if self.calls == 1
                else datetime.now(timezone.utc)
            )
            return {"quotes": [{"symbol": "AAPL", "bid": 99.98, "ask": 100.02, "quoteTime": timestamp.isoformat()}]}

    monkeypatch.setenv("PUBLIC_EXIT_QUOTE_REFRESH_ATTEMPTS", "3")
    monkeypatch.setenv("PUBLIC_EXIT_QUOTE_RETRY_SECONDS", "0")
    client = Client()

    result = await public_execution._refresh_exit_quotes(client, ["AAPL"], emergency=True)

    assert client.calls == 2
    assert result["attempts"] == 2
    assert result["resolved"]["AAPL"]["execution_quote"]["limit_price"] == 99.98
    assert result["unresolved"] == {}


@pytest.mark.asyncio
async def test_exit_quote_timeout_persists_pending_state_and_alerts(monkeypatch):
    updates = []
    alerts = []

    class Trades:
        async def update_one(self, _query, update):
            updates.append(update["$set"])

    async def emit(*args, **kwargs):
        alerts.append((args, kwargs))
        return {"sent": True}

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    monkeypatch.setattr(telegram_events, "emit_event", emit)

    await public_execution._record_exit_quote_unavailable(
        {"client_order_id": "public-aapl-1"},
        ticker="AAPL",
        reason="public_exit_quote_stale_or_unverifiable",
        quote_refresh={"attempts": 30, "configured_attempts": 30, "unresolved": {"AAPL": {"reason": "public_exit_quote_stale_or_unverifiable"}}},
    )

    assert updates[0]["emergency_exit_status"] == "PENDING_QUOTE_UNAVAILABLE"
    assert alerts[0][0][0] == "public_exit_quote_unavailable"
    assert alerts[0][1]["priority"] == "critical"


def test_public_learning_record_requires_verified_attributed_exit():
    now = datetime.now(timezone.utc)
    base = {
        "client_order_id": "order-1",
        "ticker": "AAPL",
        "status": "CLOSED",
        "broker_exit_verified": True,
        "realized_pl_pct": 4.25,
        "filled_avg_price": 100.0,
        "filled_at": now.isoformat(),
        "closed_at": (now + timedelta(minutes=20)).isoformat(),
        "strategy_id": "CORE",
        "screener_id": "CORE",
        "scanner_family": "CORE",
    }
    record = public_execution._public_learning_record(base)
    assert record["learning_eligible"] is True
    assert record["realized_pct"] == 4.25

    missing = public_execution._public_learning_record({**base, "broker_exit_verified": False})
    assert missing["learning_eligible"] is False
    assert missing["learning_exclusion_reason"] == "broker_exit_not_verified"


def test_public_learning_record_excludes_legacy_attribution():
    record = public_execution._public_learning_record({
        "client_order_id": "legacy-1", "ticker": "AAPL", "status": "CLOSED",
        "broker_exit_verified": True, "realized_pl_pct": 2.5,
        "strategy_id": "LEGACY_UNATTRIBUTED",
    })
    assert record["learning_eligible"] is False
    assert record["learning_exclusion_reason"] == "strategy_unattributed_or_legacy"


def test_verified_monitored_exit_requires_recent_active_ratchet(monkeypatch):
    now = datetime(2026, 9, 29, 19, 0, tzinfo=timezone.utc)
    monkeypatch.setenv("PUBLIC_MONITORED_EXIT_MAX_AGE_SECONDS", "180")
    covered, age = public_execution._verified_monitored_exit({
        "pm_active_stop": 9.5,
        "pm_last_ratchet_check": (now - timedelta(seconds=60)).isoformat(),
        "pm_ratchet_plan": {"enabled": True},
        "protection_state": "MONITORED_EXIT_ONLY",
    }, now=now)
    assert covered is True
    assert age == 60

    stale, _ = public_execution._verified_monitored_exit({
        "pm_active_stop": 9.5,
        "pm_last_ratchet_check": (now - timedelta(seconds=181)).isoformat(),
        "pm_ratchet_plan": {"enabled": True},
        "protection_state": "MONITORED_EXIT_ONLY",
    }, now=now)
    assert stale is False


@pytest.mark.asyncio
async def test_protection_coverage_counts_recent_terminal_monitored_exit(monkeypatch):
    now = datetime.now(timezone.utc)

    class Cursor:
        async def to_list(self, _limit):
            return [{
                "ticker": "AAPL",
                "pm_active_stop": 90.0,
                "pm_last_ratchet_check": now.isoformat(),
                "pm_ratchet_plan": {"enabled": True},
                "protection_state": "MONITORED_EXIT_ONLY",
                "status": "OPEN",
                "fill_status": "FILLED",
            }]

    class Trades:
        def find(self, _query, projection):
            assert projection["pm_active_stop"] == 1
            assert projection["pm_last_ratchet_check"] == 1
            assert projection["pm_ratchet_plan"] == 1
            return Cursor()

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    result = await public_execution.protection_coverage()
    assert result["verified_monitored_open"] == 1
    assert result["unprotected_open"] == 0


@pytest.mark.asyncio
async def test_protection_coverage_keeps_stale_known_monitoring_out_of_unresolved_bucket(monkeypatch):
    now = datetime.now(timezone.utc)

    class Cursor:
        async def to_list(self, _limit):
            return [{
                "ticker": "AAPL",
                "pm_active_stop": 90.0,
                "pm_last_ratchet_check": (now - timedelta(seconds=181)).isoformat(),
                "pm_ratchet_plan": {"enabled": True},
                "protection_state": "MONITORED_EXIT_ONLY",
                "status": "OPEN",
                "fill_status": "FILLED",
            }]

    class Trades:
        def find(self, *_args, **_kwargs):
            return Cursor()

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    result = await public_execution.protection_coverage()
    assert result["monitored_exit_only_open"] == 1
    assert result["managed_unresolved_unprotected_open"] == 0


def test_public_portfolio_order_index_uses_public_uuid_order_id():
    orders = public_execution._orders_by_id({
        "orders": [
            {"orderId": "1aadded0-0931-5a4a-9837-1165dfd4f3e7", "status": "NEW"},
            {"id": "legacy-order", "status": "FILLED"},
        ]
    })

    assert orders["1aadded0-0931-5a4a-9837-1165dfd4f3e7"]["status"] == "NEW"
    assert orders["legacy-order"]["status"] == "FILLED"


@pytest.mark.asyncio
async def test_public_order_search_overrides_incomplete_portfolio_order_feed():
    class Client:
        async def search_orders(self, **_kwargs):
            return {"orders": [{"orderId": "uuid-order", "status": "FILLED", "filledQuantity": "1"}]}

    orders, detail = await public_execution._broker_orders_with_search(
        Client(),
        {"orders": [{"orderId": "uuid-order", "status": "NEW"}]},
    )
    assert orders["uuid-order"]["status"] == "FILLED"
    assert detail["search_ok"] is True
    assert detail["source"] == "public_order_search+portfolio"


@pytest.mark.asyncio
async def test_public_order_search_failure_keeps_read_only_portfolio_fallback():
    class Client:
        async def search_orders(self, **_kwargs):
            raise RuntimeError("temporary broker outage")

    orders, detail = await public_execution._broker_orders_with_search(
        Client(),
        {"orders": [{"orderId": "uuid-order", "status": "NEW"}]},
    )
    assert orders["uuid-order"]["status"] == "NEW"
    assert detail["search_ok"] is False
    assert detail["reason"] == "public_order_search_failed:RuntimeError"


def test_public_broker_protection_capability_never_calls_fractional_or_24h_monitoring_broker_protected(monkeypatch):
    monkeypatch.delenv("PUBLIC_BRACKET_PROTECTION_ENABLED", raising=False)
    fractional = public_execution._broker_protection_capability({"amount": 6, "session": "CORE"}, 9.5)
    overnight = public_execution._broker_protection_capability({"quantity": 1, "session": "TWENTY_FOUR_HOURS"}, 9.5)
    core_whole = public_execution._broker_protection_capability({"quantity": 1, "session": "CORE"}, 9.5)
    assert fractional["mode"] == "MONITORED_EXIT_ONLY"
    assert overnight["mode"] == "MONITORED_EXIT_ONLY"
    assert core_whole["eligible"] is True
    assert core_whole["enabled"] is False
    monkeypatch.setenv("PUBLIC_BRACKET_PROTECTION_ENABLED", "true")
    enabled = public_execution._broker_protection_capability({"quantity": 1, "session": "CORE"}, 9.5)
    assert enabled["mode"] == "BROKER_OTO_STOP"
    assert enabled["enabled"] is True


@pytest.mark.asyncio
async def test_public_order_lookup_falls_back_to_portfolio_order_feed():
    class Client:
        async def get_order(self, _order_id):
            raise RuntimeError("single order endpoint unavailable")

    row = await public_execution._get_order_with_portfolio_fallback(
        Client(),
        "uuid-order",
        {"uuid-order": {"orderId": "uuid-order", "status": "NEW"}},
    )

    assert row["status"] == "NEW"


def test_public_entry_shape_obeys_session_and_notional_constraints():
    core, core_reason = public_execution._entry_order_shape(6.0, 100.0, now=datetime(2026, 9, 14, 15, tzinfo=timezone.utc))
    assert core == {"amount": 6.0, "session": "CORE"}
    assert core_reason is None

    too_small, too_small_reason = public_execution._entry_order_shape(4.0, 100.0, now=datetime(2026, 9, 14, 15, tzinfo=timezone.utc))
    assert too_small is None
    assert too_small_reason == "public_core_fractional_minimum_5_usd"

    extended, extended_reason = public_execution._entry_order_shape(6.0, 10.0, now=datetime(2026, 9, 14, 1, tzinfo=timezone.utc))
    assert extended is None
    assert extended_reason == "public_24h_requires_whole_share_within_allocation"


def test_public_exit_shape_keeps_fractional_core_exits_and_reduces_24h_positions():
    core, core_note = public_execution._exit_order_shape(1.67597, now=datetime(2026, 9, 14, 15, tzinfo=timezone.utc))
    assert core == {"quantity": 1.67597, "session": "CORE"}
    assert core_note is None

    overnight, overnight_note = public_execution._exit_order_shape(1.67597, now=datetime(2026, 9, 14, 1, tzinfo=timezone.utc))
    assert overnight == {"quantity": 1.0, "session": "TWENTY_FOUR_HOURS"}
    assert overnight_note == "public_24h_fractional_residual_core_session_required"

    unavailable, unavailable_note = public_execution._exit_order_shape(0.67597, now=datetime(2026, 9, 14, 1, tzinfo=timezone.utc))
    assert unavailable is None
    assert unavailable_note == "public_24h_exit_requires_whole_share"


def test_public_protective_order_terms_follow_broker_session_contract():
    core = public_execution._protective_order_terms(datetime(2026, 9, 14, 15, tzinfo=timezone.utc))
    assert core["time_in_force"] == "GTD"
    assert core["session"] == "CORE"
    overnight = public_execution._protective_order_terms(datetime(2026, 9, 14, 1, tzinfo=timezone.utc))
    assert overnight == {"time_in_force": "DAY", "expiration_time": None, "session": "TWENTY_FOUR_HOURS"}


def test_public_buying_power_prefers_buying_power_over_cash():
    assert public_execution._numeric_field({"cash": 0, "buyingPower": "12.00"}, {"cash", "buying_power"}) == 12.0


def test_public_cash_buying_power_does_not_use_margin_buying_power():
    assert public_execution._cash_buying_power({
        "buyingPower": {"cashOnlyBuyingPower": "4.00", "buyingPower": "100.00"}
    }) == 4.0
    assert public_execution._cash_buying_power({
        "buyingPower": {"buyingPower": "100.00"}
    }) is None


def test_public_preflight_economics_keeps_missing_fees_unknown_and_sums_known_components():
    economics = public_execution._preflight_economics({
        "orderValue": "6.00",
        "buyingPowerRequirement": "6.00",
        "estimatedCommission": "0.01",
        "estimatedExecutionFee": "0.02",
        "regulatoryFees": {"sec": "0.003", "taf": "0.001"},
        "priceIncrement": "0.01",
    })
    assert economics["order_value"] == 6.0
    assert economics["estimated_total_fees"] == 0.034
    assert economics["price_increment"] == 0.01
    assert public_execution._preflight_economics({})["estimated_total_fees"] is None


def test_public_account_permission_blocks_close_only_accounts():
    assert public_execution._account_allows_buys(
        {"accounts": [{"accountId": "acct-1", "tradePermissions": "CLOSE_ONLY"}]},
        "acct-1",
    ) == (False, "public_account_close_only")


def test_public_attribution_preserves_lottery_overlap_over_core_base():
    attribution = public_execution._strategy_attribution({
        "source_scan": "CORE",
        "scanner_family": "CORE",
        "strategy_views": [
            {"screener_id": "lottery_supernova", "family": "LOTTERY", "lane": "SUPERNOVA"},
        ],
    })
    assert attribution["strategy_id"] == "lottery_supernova"
    assert attribution["screener_id"] == "lottery_supernova"
    assert attribution["scanner_family"] == "LOTTERY"
    assert attribution["strategy_is_lottery"] is True
    assert attribution["strategy_lanes"] == ["SUPERNOVA"]


def test_pm_output_preserves_proxy_target_marker():
    row = portfolio_manager.evaluate_rows([{
        "ticker": "TEST",
        "price": 10,
        "target_blended": 14,
        "stop_loss": 9,
        "target_source": "thesis_lane_proxy_pending_validation",
        "target_is_proxy": True,
        "source_scan": "lottery_gap",
        "scanner_family": "LOTTERY",
    }], equity=1000, mode="BALANCED")[0]
    assert row["target_is_proxy"] is True
    assert row["target_source"] == "thesis_lane_proxy_pending_validation"


def test_trade_floor_learning_scope_allows_legacy_rows_only_in_paper(monkeypatch):
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    assert trade_floor_learning._trade_scope() == {"$or": [{"broker_base": "https://paper-api.alpaca.markets"}, {"broker_base": {"$exists": False}}]}

    monkeypatch.setenv("APCA_API_BASE_URL", "https://api.alpaca.markets")
    assert trade_floor_learning._trade_scope() == {"broker_base": "https://api.alpaca.markets"}


@pytest.mark.asyncio
async def test_public_execution_analytics_reports_slippage_protection_and_strategy(monkeypatch):
    class Cursor:
        def sort(self, *_args):
            return self

        async def to_list(self, _limit):
            return [{
                "broker_base": "public", "side": "BUY", "fill_status": "FILLED", "limit_price": 10,
                "filled_avg_price": 10.05, "qty_remaining": 1,
                "protective_order_id": "stop-1", "protective_order_status": "SUBMITTED", "strategy_id": "lottery_gap",
                "execution_quote": {"mid": 10.02},
                "public_preflight_economics": {"estimated_total_fees": 0.03},
            }]

    class FakeCollection:
        def find(self, *_args, **_kwargs):
            return Cursor()

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=FakeCollection()))
    result = await public_execution.analytics()
    assert result["filled_records"] == 1
    assert result["protected_filled_records"] == 1
    assert result["protection_coverage_pct"] == 100.0
    assert result["slippage_bps"]["avg"] == 50.0
    assert result["arrival_mid_slippage_bps"]["avg"] == 29.94
    assert result["unknown_side_records"] == 0
    assert result["preflight_estimated_fees_usd"] == {"n": 1, "total": 0.03, "avg": 0.03}
    assert result["by_strategy"] == {"lottery_gap": 1}


def test_public_portfolio_mark_uses_documented_public_v2_fields():
    price, pnl, timestamp = public_execution._public_position_mark({
        "lastPrice": {"lastPrice": "129.60", "timestamp": "2026-09-16T14:00:00Z"},
        "instrumentGain": {"gainPercentage": "-2.56"},
    })
    assert price == 129.60
    assert pnl == -2.56
    assert timestamp == "2026-09-16T14:00:00Z"


def test_public_position_cost_basis_uses_broker_fields_only():
    unit, total = public_execution._public_position_cost_basis({
        "costBasis": {"unitCost": "12.50", "totalCost": "6.00"},
    })
    assert unit == 12.5
    assert total == 6.0


def test_public_position_unrealized_return_uses_cost_basis_not_provider_gain_window():
    raw = {
        "costBasis": {"totalCost": "6.01"},
        "currentValue": "6.04",
        "instrumentGain": {"gainPercentage": "65.76"},
    }

    assert public_execution._public_position_unrealized_pct(raw) == 0.4992


def test_public_history_sell_match_requires_exact_quantity_and_post_entry_time():
    after = datetime(2026, 9, 3, 20, tzinfo=timezone.utc)
    transactions = [
        {"type": "TRADE", "side": "SELL", "symbol": "ABC", "quantity": "-0.5", "netAmount": "2.00", "timestamp": "2026-09-03T21:00:00Z"},
        {"type": "TRADE", "side": "SELL", "symbol": "ABC", "quantity": "-1.0", "netAmount": "4.20", "timestamp": "2026-09-03T19:00:00Z"},
        {"type": "TRADE", "side": "SELL", "symbol": "ABC", "quantity": "-1.0", "netAmount": "4.20", "timestamp": "2026-09-03T22:00:00Z"},
    ]
    match = public_execution._matching_broker_sell(transactions, ticker="ABC", quantity=1.0, after=after)
    assert match is transactions[2]


def test_quote_timestamp_far_in_the_future_is_not_fresh():
    future = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    assert safety.quote_age_seconds(future) is None
    assert safety.quote_is_fresh({"ts": future}) == (False, None)


def test_public_execution_uses_oldest_bid_ask_timestamp_not_fresh_last_trade():
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(minutes=10)).isoformat()
    row = {
        "bid": 10.00,
        "ask": 10.04,
        "lastTimestamp": now.isoformat(),
        "bidTimestamp": stale,
        "askTimestamp": stale,
        "quoteTime": now.isoformat(),
    }
    assert public_execution._execution_quote_timestamp(row) == stale
    assert safety.quote_is_fresh({"ts": public_execution._execution_quote_timestamp(row)})[0] is False


def test_public_routing_stop_rejection_is_suppressed_not_retried():
    assert public_execution._routing_rejects_stop({
        "protective_order_error": "Public API HTTP 400: Order type Stop and Stop limit are not allowed on lit exchanges."
    })


@pytest.mark.asyncio
async def test_explicit_public_exit_uses_fresh_broker_quantity_and_reconciliation_fields(monkeypatch):
    trade = {
        "client_order_id": "entry-flws-1",
        "broker_base": "public",
        "ticker": "FLWS",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": 1.0,
    }
    updates = []

    class Trades:
        async def find_one(self, *_args, **_kwargs):
            return dict(trade)

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def portfolio(self):
            return {"positions": [{"symbol": "FLWS", "quantity": 1.67597}]}

        async def quotes(self, _symbols):
            return {"quotes": [{"symbol": "FLWS", "bid": 2.67, "ask": 2.69, "quoteTime": datetime.now(timezone.utc).isoformat()}]}

        async def submit_equity_order(self, **kwargs):
            self.submitted = kwargs
            return {"preflight": {"estimatedCost": 4.5}, "order": {"orderId": "forced-exit-1"}}

    client = Client()

    async def claim(**_kwargs):
        return {"ok": True}

    async def marked(*_args, **_kwargs):
        return None

    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: client)
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", claim)
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", marked)
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "CORE")

    result = await public_execution.submit_exit_to_cash("FLWS", reason="operator_forced_exit")

    assert result["submitted"] is True
    assert result["order_id"] == "forced-exit-1"
    assert client.submitted["quantity"] == 1.67597
    assert client.submitted["session"] == "CORE"
    assert updates[0]["emergency_exit_order_id"] == "forced-exit-1"
    assert updates[0]["emergency_exit_reason"] == "operator_forced_exit"


@pytest.mark.asyncio
async def test_public_reconciliation_health_fails_closed_without_success_marker(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))
    result = await public_execution.reconciliation_health()
    assert result == {"ok": False, "reason": "public_reconciliation_not_initialized"}


@pytest.mark.asyncio
async def test_public_reconciliation_health_allows_explicit_monitored_exit_override(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return {"last_success_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))

    async def monitored_coverage():
        return {
            "filled_open": 1,
            "protected_open": 0,
            "unprotected_open": 1,
            "monitored_exit_only_open": 1,
            "legacy_unmanaged_open": 0,
            "managed_unresolved_unprotected_open": 0,
            "unresolved_unprotected_open": 0,
            "unprotected": [{"ticker": "AAPL", "status": "MONITORED_EXIT_ONLY"}],
        }

    monkeypatch.setattr(public_execution, "protection_coverage", monitored_coverage)
    monkeypatch.setenv("PUBLIC_ALLOW_MONITORED_EXIT_ONLY", "true")

    result = await public_execution.reconciliation_health()
    assert result["ok"] is True
    assert result["monitored_exit_override"] is True


@pytest.mark.asyncio
async def test_public_reconciliation_health_keeps_unknown_protection_fail_closed(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return {"last_success_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))

    async def unknown_coverage():
        return {
            "filled_open": 1,
            "protected_open": 0,
            "unprotected_open": 1,
            "monitored_exit_only_open": 0,
            "legacy_unmanaged_open": 0,
            "managed_unresolved_unprotected_open": 1,
            "unresolved_unprotected_open": 1,
            "unprotected": [{"ticker": "AAPL", "status": "MISSING"}],
        }

    monkeypatch.setattr(public_execution, "protection_coverage", unknown_coverage)
    monkeypatch.setenv("PUBLIC_ALLOW_MONITORED_EXIT_ONLY", "true")

    result = await public_execution.reconciliation_health()
    assert result["ok"] is False
    assert result["entry_ok"] is False
    assert result["reason"] == "public_protection_coverage_incomplete"


@pytest.mark.asyncio
async def test_public_reconciliation_health_does_not_globally_lock_entries_for_stale_known_monitoring(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return {"last_success_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))

    async def stale_monitored_coverage():
        return {
            "filled_open": 1,
            "protected_open": 0,
            "unprotected_open": 1,
            "monitored_exit_only_open": 1,
            "legacy_unmanaged_open": 0,
            "managed_unresolved_unprotected_open": 1,
            "unresolved_unprotected_open": 0,
            "unprotected": [{"ticker": "AAPL", "status": "MONITORED_EXIT_STALE_OR_INCOMPLETE"}],
        }

    monkeypatch.setattr(public_execution, "protection_coverage", stale_monitored_coverage)

    result = await public_execution.reconciliation_health()

    assert result["ok"] is False
    assert result["entry_ok"] is True
    assert result["entry_warning"] == "public_existing_positions_monitor_stale"


@pytest.mark.asyncio
async def test_public_reconciliation_health_allows_imported_legacy_positions_without_unblocking_managed_gaps(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return {"last_success_at": datetime.now(timezone.utc).isoformat()}

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))

    async def legacy_coverage():
        return {
            "filled_open": 2,
            "protected_open": 0,
            "unprotected_open": 2,
            "monitored_exit_only_open": 1,
            "legacy_unmanaged_open": 1,
            "managed_unresolved_unprotected_open": 0,
            "unresolved_unprotected_open": 1,
            "unprotected": [
                {"ticker": "AAPL", "status": "MONITORED_EXIT_ONLY", "legacy_unmanaged": False},
                {"ticker": "OLD", "status": "LEGACY_UNMANAGED", "legacy_unmanaged": True},
            ],
        }

    monkeypatch.setattr(public_execution, "protection_coverage", legacy_coverage)
    monkeypatch.setenv("PUBLIC_ALLOW_MONITORED_EXIT_ONLY", "true")

    result = await public_execution.reconciliation_health()
    assert result["ok"] is True
    assert result["legacy_unmanaged_override"] is True
    assert "public_legacy_positions_unmanaged" in result["warnings"]


@pytest.mark.asyncio
async def test_execution_freshness_refreshes_approved_rows_before_execution(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def quotes(self, symbols):
            assert symbols == ["AAPL"]
            return {"quotes": [{
                "symbol": "AAPL",
                    "bid": 150.15,
                    "ask": 150.25,
                "quoteTime": datetime.now(timezone.utc).isoformat(),
            }]}

    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: FakeClient())
    result = await public_execution.refresh_execution_freshness([{
        "ticker": "AAPL", "action": "STARTER", "allocation_usd": 6,
    }])

    assert result["attempts"] == 2
    assert result["fresh"] == 1
    assert result["stale"] == 0
    assert result["rows"][0]["source"] == "public"
    assert result["rows"][0]["fresh"] is True


@pytest.mark.asyncio
async def test_execution_freshness_keeps_newer_stale_public_quote(monkeypatch):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def quotes(self, _symbols):
            return {"quotes": [{
                "symbol": "AAPL",
                "ask": 150.25,
                "quoteTime": (datetime.now(timezone.utc) - timedelta(seconds=180)).isoformat(),
            }]}

    async def older_alpaca(*_args, **_kwargs):
        return {
            "price": 149.0,
            "source": "alpaca_latest_trade_iex",
            "provider_ts": (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat(),
            "age_seconds": 600,
            "execution_eligible": False,
        }

    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: FakeClient())
    monkeypatch.setattr("services.pricer._alpaca_trade_meta", older_alpaca)
    result = await public_execution.refresh_execution_freshness([{
        "ticker": "AAPL", "action": "STARTER", "allocation_usd": 6,
    }])

    assert result["rows"][0]["source"] == "public"
    assert 150 <= result["rows"][0]["age_seconds"] <= 190


@pytest.mark.asyncio
async def test_public_execution_uses_fresh_quote_and_submits_order(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.submitted = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def portfolio(self):
            return {"positions": [], "buyingPower": {"cashOnlyBuyingPower": "12.00"}}

        async def accounts(self):
            return {"accounts": [{"accountId": "acct-1", "tradePermissions": "FULL"}]}

        async def quotes(self, symbols):
            row = {"symbol": "AAPL", "bid": 150.15, "ask": 150.25, "quoteTime": datetime.now(timezone.utc).isoformat()}
            return {"quotes": [row]}

        async def submit_equity_order(self, **kwargs):
            self.submitted.append(kwargs)
            return {"preflight": {"outcome": "SUCCESS"}, "order": {"orderId": "order-1"}}

    class FakeCollection:
        def __init__(self):
            self.docs = []

        async def insert_one(self, _doc):
            self.docs.append(_doc)
            return None

    fake_client = FakeClient()
    fake_trades = FakeCollection()
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(public_execution.public_api, "config", lambda: SimpleNamespace(account_id="acct-1"))
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "reconciliation_health", lambda: _healthy())
    monkeypatch.setattr(public_execution.execution_safety, "add_risk_allowed", lambda _scope: _allowed())
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", lambda **_kwargs: _claimed())
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", lambda *_args, **_kwargs: _marked())
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=fake_trades))
    monkeypatch.setattr(public_execution, "log_activity", _marked)
    monkeypatch.setattr(public_execution, "_public_session_now", lambda: "CORE")
    monkeypatch.setattr("services.trading_halts.fetch_halts", lambda: _clear_halts())
    monkeypatch.setenv("LOTTERY_DAY2_LIVE_AUTOMATION_ENABLED", "true")
    monkeypatch.setenv("LOTTERY_DAY2_MIN_PM_SCORE", "60")

    pm_row = portfolio_manager.evaluate_rows([{
        "ticker": "AAPL",
        "price": 150.25,
        "target_blended": 180,
        "stop_loss": 140,
        "source_scan": "lottery_gap",
        "scanner_family": "LOTTERY",
        "signals": ["GAP/SURGE", "RVOL"],
        "signal_groups": ["MOMENTUM", "VOLUME", "CATALYST"],
        "independent_signal_count": 3,
        "strategy_views": [{"screener_id": "lottery_gap", "family": "LOTTERY", "lane": "DAY2_CONTINUATION"}],
    }], equity=1000, mode="BALANCED")[0]
    pm_row.update({
        "action": "STARTER",
        "allocation_usd": 6,
        "pm_score": 80,
        "target_is_proxy": True,
        "target_source": "thesis_lane_proxy_pending_validation",
        "ratchet_plan": {"enabled": True, "exit_policy": "STOP_RATCHET_ONLY"},
    })
    result = await public_execution.execute_pm_equity(
        [pm_row],
        cycle_id="cycle-1",
    )

    assert len(result["executed"]) == 1
    assert result["rejected"] == []
    assert fake_client.submitted[0]["session"] == "CORE"
    assert fake_trades.docs[0]["strategy_id"] == "lottery_gap"
    assert fake_trades.docs[0]["screener_id"] == "lottery_gap"
    assert fake_trades.docs[0]["scanner_family"] == "LOTTERY"
    assert fake_trades.docs[0]["strategy_lanes"] == ["DAY2_CONTINUATION"]
    assert fake_trades.docs[0]["strategy_attribution"]["strategy_id"] == "lottery_gap"
    assert fake_trades.docs[0]["current_stop"] == 140.0
    assert fake_trades.docs[0]["pm_active_stop"] == 140.0
    assert fake_trades.docs[0]["execution_target_mode"] == "RATCHET_ONLY_PROXY_TARGET"
    assert fake_trades.docs[0]["entry_decision"]["evidence"]["signal_groups"] == ["MOMENTUM", "VOLUME", "CATALYST"]
    assert fake_trades.docs[0]["entry_decision"]["pm"]["score"] == 80
    assert fake_trades.docs[0]["entry_decision"]["day2_observation"]["quality"] == "UNAVAILABLE"


def test_day2_entry_gate_defaults_to_shadow_and_requires_distinct_confirmation(monkeypatch):
    row = {
        "source_scan": "lottery_day2_continuation",
        "scanner_family": "LOTTERY",
        "strategy_views": [{"screener_id": "lottery_day2_continuation", "family": "LOTTERY", "lane": "DAY2_CONTINUATION"}],
        "signal_groups": ["MOMENTUM", "VOLUME"],
        "pm_score": 80,
    }
    allowed, reason = public_execution._day2_entry_gate(row)
    assert allowed is False
    assert reason == "lottery_day2_shadow_until_edge_proven"

    monkeypatch.setenv("LOTTERY_DAY2_LIVE_AUTOMATION_ENABLED", "true")
    allowed, reason = public_execution._day2_entry_gate(row)
    assert allowed is False
    assert reason == "lottery_day2_requires_distinct_confirmation"

    row["signal_groups"].append("CATALYST")
    row["pm_score"] = 59
    allowed, reason = public_execution._day2_entry_gate(row)
    assert allowed is False
    assert reason == "lottery_day2_pm_score_below_minimum"

    row["pm_score"] = 60
    allowed, reason = public_execution._day2_entry_gate(row)
    assert allowed is True
    assert reason is None


def test_public_learning_excludes_immediate_or_unconfirmed_exit_from_strategy_tuning():
    now = datetime.now(timezone.utc)
    immediate = {
        "client_order_id": "public-immediate",
        "broker_base": "public",
        "ticker": "ABC",
        "status": "CLOSED",
        "strategy_id": "lottery_day2_continuation",
        "screener_id": "lottery_day2_continuation",
        "scanner_family": "LOTTERY",
        "strategy_lanes": ["DAY2_CONTINUATION"],
        "submitted_at": now.isoformat(),
        "filled_at": now.isoformat(),
        "closed_at": (now + timedelta(seconds=1)).isoformat(),
        "close_reason": "public_position_absent",
        "broker_exit_unconfirmed": True,
        "broker_exit_verified": True,
        "realized_pl_pct": -15.5,
    }
    record = public_execution._public_learning_record(immediate)
    assert record["outcome_class"] == public_execution.OUTCOME_EXECUTION_ANOMALY
    assert record["learning_eligible"] is False
    assert record["exit_learning_eligible"] is False


def test_public_learning_accepts_only_verified_measured_thesis_outcome():
    now = datetime.now(timezone.utc)
    measured = {
        "client_order_id": "public-thesis",
        "broker_base": "public",
        "ticker": "ABC",
        "status": "CLOSED",
        "strategy_id": "core_momentum",
        "screener_id": "core_momentum",
        "scanner_family": "CORE",
        "submitted_at": now.isoformat(),
        "filled_at": now.isoformat(),
        "closed_at": (now + timedelta(minutes=20)).isoformat(),
        "close_reason": "pm_thesis_review_exit",
        "broker_exit_verified": True,
        "realized_pl_pct": 6.25,
        "realized_pnl": 0.38,
    }
    record = public_execution._public_learning_record(measured)
    assert record["outcome_class"] == public_execution.OUTCOME_THESIS_WIN
    assert record["learning_eligible"] is True
    assert record["entry_learning_eligible"] is True


@pytest.mark.asyncio
async def test_public_reconcile_replaces_protective_stop_after_partial_fill(monkeypatch):
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _limit):
            return list(self.rows)

    class FakeClient:
        def __init__(self):
            self.cancelled = []
            self.submitted = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get_order(self, _order_id):
            return {"status": "PARTIALLY_FILLED", "filledQuantity": 2, "averagePrice": 10.0}

        async def cancel_order(self, order_id):
            self.cancelled.append(order_id)
            return {"ok": True}

        async def submit_equity_order(self, **kwargs):
            self.submitted.append(kwargs)
            return {"preflight": {"outcome": "SUCCESS"}, "order": {"orderId": "new-stop"}}

        async def portfolio(self):
            return {"positions": [{"symbol": "AAPL", "quantity": 2}]}

    class FakeCollection:
        def __init__(self, rows):
            self.rows = rows
            self.updates = []

        def find(self, query, *_args):
            if query.get("fill_status") == "PENDING":
                return Cursor(self.rows)
            return Cursor(self.rows)

        async def update_one(self, query, update, **_kwargs):
            self.updates.append((query, update))

    class FakeState:
        async def update_one(self, *_args, **_kwargs):
            return None

    trade = {
        "client_order_id": "public-entry-1",
        "public_order_id": "entry-1",
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "PENDING",
        "ticker": "AAPL",
        "protective_order_id": "old-stop",
        "protective_order_qty": 1.0,
        "pm_active_stop": 9.0,
        "current_stop": 9.0,
    }
    fake_client = FakeClient()
    fake_trades = FakeCollection([trade])
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: fake_client)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", lambda **_kwargs: _claimed())
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", lambda *_args, **_kwargs: _marked())
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=fake_trades, bot_state=FakeState()))
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "TWENTY_FOUR_HOURS")

    result = await public_execution.reconcile()

    assert result["ok"] is True
    assert fake_client.cancelled == ["old-stop"]
    assert len(fake_client.submitted) == 1
    assert fake_client.submitted[0]["quantity"] == 2
    assert fake_client.submitted[0]["session"] == "TWENTY_FOUR_HOURS"


async def _allowed():
    return True, {"trading_enabled": True}


async def _claimed():
    return {"ok": True}


async def _marked(*_args, **_kwargs):
    return None


async def _healthy():
    return {"ok": True, "age_seconds": 1}


async def _clear_halts():
    return {"ok": True, "halts": []}
