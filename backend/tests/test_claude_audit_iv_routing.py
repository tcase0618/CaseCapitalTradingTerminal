"""MED-014: elevated observed IV cannot pass through missing rank gates."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import execution_gate, options_desk, options_engine, safety
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.mark.parametrize('action,score', [('STARTER', 80), ('WATCH', 48)])
@pytest.mark.parametrize('strategy', ['LONG_CALL_SCOUT', 'LONG_CALL_EVENT_SCOUT'])
def test_desk_does_not_approve_high_iv_without_rank(action, score, strategy):
    pm = {'action': action, 'pm_score': score, 'risk_reward': 2}
    row = {'options': {'strategy': strategy, 'iv_rank': None, 'atm_iv': 1.2,
                       'options_intent': True, 'contract': {'symbol': 'OFFLINE'}}}
    route, reasons = options_desk._route(pm, row)
    assert route not in {'OPTION', 'BOTH'}
    assert 'IV' in ' '.join(reasons)
    assert not options_desk._pm_can_consider_options(pm, row)


@pytest.mark.parametrize('signals,days,score,squeeze', [
    (['high_short_interest'], 10, 80, 90), (['insider_cluster_buy'], 60, 80, 60),
    (['congressional_buy'], 30, 80, 0), (['contract_surge'], 30, 60, 0),
    (['unusual_flow'], 30, 60, 0), (['bearish'], 30, 80, 0),
    (['insider_cluster_buy'], 150, 80, 0),
])
def test_non_event_selector_does_not_recommend_high_iv_premium(signals, days, score, squeeze):
    result = options_engine.select_strategy({
        'signals': signals, 'risk': {'level': 'LOW'}, 'score': score, 'risk_reward': 2,
        'squeeze': {'score': squeeze}, 'time_target': {'days_remaining': days},
    }, {'iv_rank': None, 'atm_iv': 1.2})
    assert result['strategy'] == 'AVOID_OPTIONS'


@pytest.mark.parametrize('source', ['ticket', 'research_options', 'execution_options'])
@pytest.mark.parametrize('evidence', [{'atm_iv': .9}, {'absolute_iv_label': 'HIGH'}])
def test_cached_ready_ticket_is_blocked_from_any_high_iv_packet(monkeypatch, source, evidence):
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(options_desk, 'paper_only', lambda: True)
    ticket = {'route': 'OPTION', 'strategy': 'LONG_CALL_SCOUT', 'contracts': 1,
              'risk_budget': 350, 'data_provider': 'ALPACA_OPTIONS', 'data_quality': 'EXECUTION_GRADE',
              'manual_fire_ready': True, 'instrument': {'kind': 'single_leg', 'ask': 1,
              'bid': .99, 'premium': 1, 'max_loss': 100, 'delta': .5,
              'provider_delta_present': True, 'open_interest': 500, 'volume': 200}}
    assert options_desk._normalize_candidate_execution_state(ticket)['manual_fire_ready']
    if source == 'ticket':
        ticket.update(evidence)
    else:
        ticket[source] = evidence
    result = options_desk._normalize_candidate_execution_state(ticket)
    assert not result['manual_fire_ready']
    assert 'unknown_rank_elevated_absolute_iv' in result['blocked_reasons']


@pytest.mark.asyncio
async def test_manual_entry_rechecks_iv_before_gate_or_broker(monkeypatch):
    ticket = {'candidate_id': 'offline', 'cycle_id': 'offline-cycle', 'manual_fire_ready': True,
              'iv_rank': None, 'research_options': {'atm_iv': 1.2}}
    monkeypatch.setattr(options_desk, 'get_db', lambda: SimpleNamespace(
        options_desk_candidates=MemoryCollection([ticket])))
    monkeypatch.setattr(safety, 'trading_enabled', AsyncMock(return_value=(True, {})))
    gate = AsyncMock(side_effect=AssertionError('must stop before broker checks'))
    monkeypatch.setattr(execution_gate, 'check', gate)
    result = await options_desk.execute('offline')
    assert result['reason'] == 'unknown_rank_elevated_absolute_iv'
    gate.assert_not_awaited()


@pytest.mark.parametrize('rank,iv', [(50, 1.2), (None, .2), (None, None)])
def test_existing_known_rank_and_non_elevated_routes_unchanged(rank, iv):
    pm = {'action': 'STARTER', 'pm_score': 80, 'risk_reward': 2}
    row = {'options': {'strategy': 'LONG_CALL_SCOUT', 'iv_rank': rank, 'atm_iv': iv,
                       'contract': {'symbol': 'OFFLINE'}}}
    assert options_desk._route(pm, row)[0] == 'BOTH'
    assert options_desk._pm_can_consider_options(pm, row)


@pytest.mark.parametrize('rank', [None, True, False, float('nan'), float('inf'), -1, 101])
def test_invalid_rank_cannot_bypass_elevated_absolute_iv(rank):
    opts = {'iv_rank': rank, 'atm_iv': 1.2, 'strategy': 'LONG_CALL_SCOUT',
            'contract': {'symbol': 'OFFLINE'}}
    pm = {'action': 'STARTER', 'pm_score': 80, 'risk_reward': 2}
    assert options_desk._route(pm, {'options': opts})[0] == 'EQUITY'
    assert options_engine.select_strategy({'signals': ['unusual_flow'], 'score': 80,
        'risk_reward': 2}, opts)['strategy'] == 'AVOID_OPTIONS'


@pytest.mark.asyncio
async def test_fresh_execution_iv_blocks_candidate_even_if_research_iv_is_moderate(monkeypatch):
    from services import options_contract_learning, portfolio_manager
    monkeypatch.setattr(options_desk, 'get_db', lambda: SimpleNamespace(
        options_lane_throttle=MemoryCollection()))
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(options_desk, 'paper_only', lambda: True)
    monkeypatch.setattr(portfolio_manager, '_option_view', lambda *args: 'CALL_ALLOWED')
    monkeypatch.setattr(options_contract_learning, 'record_selection', AsyncMock(return_value=None))
    instrument = {'symbol': 'OFFLINE', 'ask': 1, 'bid': .99, 'premium': 1, 'max_loss': 100,
                  'delta': .5, 'provider_delta_present': True, 'open_interest': 500, 'volume': 200}
    research = {'strategy': 'LONG_CALL_SCOUT', 'iv_rank': None, 'atm_iv': .2, 'contract': instrument}
    execution = {**research, 'atm_iv': 1.2, 'data_provider': 'ALPACA_OPTIONS',
                 'data_quality': 'EXECUTION_GRADE'}
    monkeypatch.setattr(options_engine, 'analyze_ticker', AsyncMock(return_value=execution))
    scan = {'cycle_id': 'offline-cycle', 'finished_at': '2026-10-29T12:00:00+00:00',
            'results': [{'ticker': 'TEST', 'options': research}]}
    result = await options_desk.build_candidates(persist=False, scan=scan,
        pm_recommendations=[{'ticker': 'TEST', 'action': 'STARTER', 'pm_score': 80, 'risk_reward': 2}])
    ticket = result['candidates'][0]
    assert not ticket['manual_fire_ready']
    assert 'unknown_rank_elevated_absolute_iv' in ticket['blocked_reasons']
    assert ticket['research_options'] == research
    assert ticket['execution_options'] == execution
    assert ticket['iv_rank'] is None
