from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import public_api, public_execution
from test_claude_audit_exit_recovery import exit_case  # noqa: F401


@pytest.mark.asyncio
async def test_unknown_submission_replays_identical_request_after_one_minute(exit_case):
    trade, client, _, alerts = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    assert not (await public_execution.process_protective_exits())['ok']
    original = dict(client.submissions[0])
    trade['emergency_exit_submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    client.failure = None
    client.bid = 8.5
    result = await public_execution.process_protective_exits()
    assert len(client.submissions) == 2
    assert client.submissions[1] == original
    assert trade['emergency_exit_status'] == 'SUBMITTED'
    assert result['deferred'] and alerts
    await public_execution.process_protective_exits()
    assert len(client.submissions) == 2


@pytest.mark.asyncio
async def test_unresolved_unknown_is_degraded_and_alerted_on_each_monitor_pass(exit_case):
    _, client, _, alerts = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    result = await public_execution.process_protective_exits()
    assert not result['ok'] and result['errors']
    assert len(alerts) >= 2 and len(client.submissions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('changed_quantity,missing_request', [(True, False), (False, True)])
async def test_unknown_replay_fails_closed_without_original_request_or_unchanged_holding(exit_case, changed_quantity, missing_request):
    trade, client, _, _ = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    trade['emergency_exit_submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    if changed_quantity:
        client.quantity = .5
    if missing_request:
        trade.pop('emergency_exit_request', None)
    client.failure = None
    result = await public_execution.process_protective_exits()
    assert not result['ok'] and len(client.submissions) == 1


@pytest.mark.asyncio
async def test_unknown_order_found_at_broker_is_not_replayed(exit_case):
    trade, client, _, _ = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    trade['emergency_exit_submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    client.orders[trade['emergency_exit_order_id']] = {'status': 'NEW'}
    client.failure = None
    await public_execution.process_protective_exits()
    assert len(client.submissions) == 1


@pytest.mark.asyncio
async def test_unknown_replay_keeps_original_account_and_blocks_account_change(exit_case):
    trade, client, _, _ = exit_case
    client.cfg = SimpleNamespace(account_id='offline-original-account')
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    assert trade['emergency_exit_request']['account_id'] == 'offline-original-account'
    trade['emergency_exit_submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    client.cfg.account_id = 'offline-other-account'
    client.failure = None
    assert not (await public_execution.process_protective_exits())['ok']
    assert len(client.submissions) == 1


@pytest.mark.asyncio
async def test_unknown_replay_retry_is_throttled_and_never_generates_new_id(exit_case):
    trade, client, _, _ = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    trade['emergency_exit_submitted_at'] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    await public_execution.process_protective_exits()
    await public_execution.process_protective_exits()
    assert len(client.submissions) == 2 and client.submissions[0] == client.submissions[1]
