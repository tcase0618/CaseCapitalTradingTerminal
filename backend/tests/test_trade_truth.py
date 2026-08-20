from __future__ import annotations

from services import trade_truth


def test_parse_trade_floor_sell_reason():
    assert (
        trade_truth.parse_trade_floor_sell_reason(
            "tf-sell-NN-phase1_target_hit-1780000000",
            "NN",
        )
        == "phase1_target_hit"
    )
    assert trade_truth.parse_trade_floor_sell_reason("manual-NN", "NN") is None


def test_resolves_weighted_alpaca_sell_fills():
    trade = {
        "ticker": "BKKT",
        "filled_avg_price": 7.85,
        "filled_at": "2026-08-13T13:30:00+00:00",
    }
    orders = [
        {
            "id": "old",
            "symbol": "BKKT",
            "side": "sell",
            "filled_at": "2026-08-13T13:00:00+00:00",
            "filled_avg_price": "9.99",
            "filled_qty": "1",
            "client_order_id": "tf-sell-BKKT-before-entry-1",
        },
        {
            "id": "a",
            "symbol": "BKKT",
            "side": "sell",
            "filled_at": "2026-08-13T14:00:00+00:00",
            "filled_avg_price": "6.85",
            "filled_qty": "2",
            "client_order_id": "tf-sell-BKKT-hard_stop-1780000001",
        },
        {
            "id": "b",
            "symbol": "BKKT",
            "side": "sell",
            "filled_at": "2026-08-13T14:02:00+00:00",
            "filled_avg_price": "7.00",
            "filled_qty": "1",
            "client_order_id": "tf-sell-BKKT-hard_stop-1780000002",
        },
    ]

    truth = trade_truth.resolve_equity_close_from_alpaca_sells(trade, orders)

    assert truth["fill_truth_status"] == "verified_alpaca_sell_fill"
    assert truth["fill_truth_source"] == "alpaca_orders"
    assert truth["learning_excluded"] is False
    assert truth["close_reason"] == "hard_stop"
    assert truth["exit_price"] == 6.9
    assert truth["realized_pct"] == -12.1019
    assert truth["exit_order_ids"] == ["a", "b"]


def test_missing_alpaca_sell_fill_is_learning_excluded():
    trade = {
        "ticker": "AMRC",
        "filled_avg_price": 27.42,
        "filled_at": "2026-08-13T13:30:00+00:00",
    }

    truth = trade_truth.resolve_equity_close_from_alpaca_sells(
        trade,
        [],
        fallback_price=25.10,
    )

    assert truth["fill_truth_status"] == "unverified_no_alpaca_sell_fill"
    assert truth["fill_truth_source"] == "fallback_mark"
    assert truth["learning_excluded"] is True
    assert truth["learning_excluded_reason"] == "missing_verified_alpaca_sell_fill"
    assert truth["realized_pct"] == -8.461
