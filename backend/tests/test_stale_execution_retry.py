from services import terminal_cycle


def test_stale_execution_retry_only_accepts_quote_freshness_failures():
    assert terminal_cycle._is_stale_public_entry_rejection("public_execution_quote_stale_or_unverifiable")
    assert terminal_cycle._is_stale_public_entry_rejection("public_final_quote_unavailable")
    assert not terminal_cycle._is_stale_public_entry_rejection("public_buying_power_insufficient")
    assert not terminal_cycle._is_stale_public_entry_rejection("public_position_exists")
    assert not terminal_cycle._is_stale_public_entry_rejection("public_invalid_protective_stop")
