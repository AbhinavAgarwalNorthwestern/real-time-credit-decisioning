"""Unit tests for the contextual bandit chooser."""

from __future__ import annotations

import numpy as np
from decisioner.bandit import (
    Action,
    BanditContext,
    _expected_profit_fraud_check,
    choose,
)
from decisioner.inference import ArmPrediction, UpliftPrediction


def _ctx(
    *,
    p_accept: float = 0.5,
    delta_spend: float = 100.0,
    p_default: float = 0.01,
    exposure: float = 5000.0,
    fraud_score: float = 0.0,
) -> BanditContext:
    return BanditContext(
        customer_id='cust-001',
        segment_id=0,
        uplift=UpliftPrediction(
            control=ArmPrediction(p_accept=0.0, delta_spend=0.0, p_default=0.0),
            treated=ArmPrediction(
                p_accept=p_accept, delta_spend=delta_spend, p_default=p_default
            ),
        ),
        exposure=exposure,
        fraud_score=fraud_score,
    )


def test_high_uplift_selects_offer_cli() -> None:
    d = choose(_ctx(p_accept=0.9, delta_spend=500.0, p_default=0.001))
    assert d.action == Action.OFFER_CLI


def test_high_fraud_selects_fraud_check() -> None:
    d = choose(_ctx(p_accept=0.0, delta_spend=0.0, p_default=0.0, fraud_score=0.95))
    assert d.action == Action.FRAUD_CHECK


def test_zero_uplift_zero_fraud_selects_nothing() -> None:
    d = choose(_ctx(p_accept=0.0, delta_spend=0.0, p_default=0.0, fraud_score=0.0))
    assert d.action == Action.NOTHING


def test_propensities_sum_to_one() -> None:
    rng = np.random.default_rng(seed=42)
    for _ in range(20):
        c = _ctx(
            p_accept=float(rng.uniform(0, 1)),
            delta_spend=float(rng.uniform(0, 500)),
            p_default=float(rng.uniform(0, 0.1)),
            fraud_score=float(rng.uniform(0, 1)),
        )
        profit_offer = c.uplift.expected_profit_treated(c.exposure)
        profit_fraud = _expected_profit_fraud_check(c.fraud_score)
        profits = np.array([0.0, profit_offer, profit_fraud])
        z = profits - profits.max()
        soft = np.exp(z / 10.0)
        soft /= soft.sum()
        assert abs(float(soft.sum()) - 1.0) < 1e-9


def test_propensity_never_zero() -> None:
    for c in [
        _ctx(p_accept=1.0, delta_spend=1000.0, p_default=0.0, fraud_score=0.0),
        _ctx(p_accept=0.0, delta_spend=0.0, p_default=0.0, fraud_score=1.0),
        _ctx(p_accept=0.0, delta_spend=0.0, p_default=0.0, fraud_score=0.0),
    ]:
        d = choose(c)
        assert d.propensity > 0.0, f'propensity must be >0 for OPE; got {d.propensity}'


def test_deterministic() -> None:
    c = _ctx(p_accept=0.6, delta_spend=200.0, p_default=0.02, fraud_score=0.3)
    first = choose(c)
    for _ in range(50):
        again = choose(c)
        assert again.action == first.action
        assert again.propensity == first.propensity
        assert again.expected_profit == first.expected_profit
