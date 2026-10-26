"""Unknown historical IV must not become favorable earnings evidence."""
import pytest

from services import earnings_engine, options_engine


PROFILES = {
    'bullish': {'eps_history': [{'surprise_pct': 10}], 'momentum_20d_pct': 10,
                'revenue_accel': 10, 'short_pct': 0.01},
    'neutral': {},
    'bearish': {'eps_history': [{'surprise_pct': -10}], 'momentum_20d_pct': -10,
                'revenue_accel': -10, 'short_pct': 0.3},
}


@pytest.mark.parametrize('profile', PROFILES)
@pytest.mark.parametrize('rank', [None, float('nan'), float('inf'), float('-inf'), -1, 101, True, False, 'bad'])
def test_unavailable_earnings_rank_is_not_cheap_or_a_premium_recommendation(profile, rank):
    result = earnings_engine._compute_beat_probability({**PROFILES[profile], 'iv_rank': rank})
    assert result['iv_cheap'] is False
    assert result['strategy'] == 'AVOID'
    assert result['iv_evidence'] == 'UNKNOWN'
    assert result['prob_pct'] == earnings_engine._compute_beat_probability({**PROFILES[profile], 'iv_rank': 50})['prob_pct']


@pytest.mark.parametrize('rank', [0, 20, 34.9, 35, 80, 100])
@pytest.mark.parametrize('profile', PROFILES)
def test_known_earnings_rank_keeps_existing_thresholds(profile, rank):
    result = earnings_engine._compute_beat_probability({**PROFILES[profile], 'iv_rank': rank})
    expected = ('LONG CALL' if rank < 35 else 'CALL SPREAD (sell rich IV)') if profile == 'bullish' else (
        'AVOID' if profile == 'neutral' else 'BEAR PUT SPREAD'
    )
    assert result['strategy'] == expected
    assert result['iv_cheap'] is (rank < 35)


@pytest.mark.parametrize('atm_iv,days,expected', [(1.2, 2, 'SEVERE'), (1.2, 5, 'SEVERE'),
                                              (1.2, 10, 'HIGH'), (0.7, 5, 'HIGH'), (0.7, 10, 'HIGH')])
def test_absolute_iv_preserves_crush_warning_without_inventing_rank(atm_iv, days, expected):
    chain = {'iv_rank': None, 'atm_iv': atm_iv}
    result = options_engine.assess_iv_crush_risk(
        {'signals': ['upcoming_earnings'], 'time_target': {'days_remaining': days}}, chain,
    )
    assert result['crush_risk'] == expected
    assert result['iv_evidence'] == 'ABSOLUTE_IV'
    assert chain['iv_rank'] is None


def test_low_absolute_iv_does_not_claim_historical_cheapness():
    result = options_engine.assess_iv_crush_risk(
        {'signals': ['upcoming_earnings'], 'time_target': {'days_remaining': 5}},
        {'iv_rank': None, 'atm_iv': 0.2},
    )
    assert result['crush_risk'] == 'UNKNOWN'


@pytest.mark.parametrize('atm_iv', [0.7, 1.2])
@pytest.mark.parametrize('score,rr,expected', [(60, 1.6, 'LONG_CALL_EVENT_SCOUT'), (50, 1.25, 'AVOID_OPTIONS')])
def test_elevated_absolute_earnings_iv_uses_existing_conservative_lane(atm_iv, score, rr, expected):
    chain = {'iv_rank': None, 'atm_iv': atm_iv}
    result = options_engine.select_strategy({
        'signals': ['upcoming_earnings'], 'score': score, 'risk_reward': rr,
        'time_target': {'days_remaining': 5},
    }, chain)
    assert result['strategy'] == expected
    assert chain['iv_rank'] is None
