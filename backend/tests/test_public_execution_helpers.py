from services import public_execution


def test_public_trade_quantity_prefers_reconciled_remaining_quantity():
    assert public_execution._qty({"qty_remaining": 1.25, "quantity": 0.0}) == 1.25


def test_public_trade_quantity_supports_portfolio_quantity():
    assert public_execution._qty({"quantity": "2.5"}) == 2.5


def test_public_buying_power_prefers_buying_power_over_cash():
    assert public_execution._numeric_field({"cash": 0, "buyingPower": "12.00"}, {"cash", "buying_power"}) == 12.0
