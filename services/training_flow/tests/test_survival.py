"""Unit tests for the discrete-time hazard survival model (Phase D A7)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.survival import (
    HazardModel,
    SurvivalCurve,
    fit_discrete_hazard,
    peak_hazard_month,
    predict_survival,
    survival_to_dataframe,
)


def _synthetic_survival_cohort(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """Construct a cohort where higher credit_score → later default + less default."""
    rng = np.random.default_rng(seed=seed)
    credit_score = rng.uniform(500, 850, size=n)
    # P(default within 24 mo) decreases with credit score
    p_default_24 = 0.5 - 0.0008 * (credit_score - 500)
    p_default_24 = np.clip(p_default_24, 0.05, 0.5)
    defaulted = rng.binomial(1, p_default_24).astype(int)
    # Higher score → later default (right-shifted)
    base_month = (650 - credit_score) / 20  # ranges roughly 0-7
    default_month = np.clip(rng.normal(8.0 + base_month, 4.0), 1, 24).astype(int)
    default_month = np.where(defaulted == 1, default_month, 0)
    return pd.DataFrame(
        {
            'customer_id': [f'cust-{i:04d}' for i in range(n)],
            'credit_score': credit_score,
            'defaulted': defaulted,
            'default_month': default_month,
        }
    )


def test_fit_returns_hazard_model() -> None:
    df = _synthetic_survival_cohort(n=300)
    model = fit_discrete_hazard(
        df,
        'customer_id',
        'defaulted',
        'default_month',
        feature_cols=['credit_score'],
        horizon_months=24,
    )
    assert isinstance(model, HazardModel)
    assert 'month' in model.feature_names
    assert 'credit_score' in model.feature_names
    assert model.horizon_months == 24


def test_predict_survival_curves_shape_and_monotonic() -> None:
    df = _synthetic_survival_cohort(n=200)
    model = fit_discrete_hazard(
        df,
        'customer_id',
        'defaulted',
        'default_month',
        feature_cols=['credit_score'],
        horizon_months=12,
    )
    curves = predict_survival(model, df.head(5), 'customer_id', ['credit_score'])
    assert len(curves) == 5
    for c in curves:
        assert isinstance(c, SurvivalCurve)
        assert len(c.hazard) == 12
        assert len(c.survival) == 12
        # Survival monotonically decreasing
        for i in range(len(c.survival) - 1):
            assert c.survival[i] >= c.survival[i + 1] - 1e-9
        # Cumulative default monotonically increasing
        for i in range(len(c.cumulative_default) - 1):
            assert c.cumulative_default[i] <= c.cumulative_default[i + 1] + 1e-9
        # All in [0, 1]
        for h in c.hazard:
            assert 0.0 <= h <= 1.0
        for s in c.survival:
            assert 0.0 <= s <= 1.0


def test_higher_credit_score_lower_predicted_default() -> None:
    """At month 24, lower credit_score → higher cumulative default."""
    df = _synthetic_survival_cohort(n=1000)
    model = fit_discrete_hazard(
        df,
        'customer_id',
        'defaulted',
        'default_month',
        feature_cols=['credit_score'],
        horizon_months=24,
    )
    probe = pd.DataFrame(
        {
            'customer_id': ['low', 'high'],
            'credit_score': [550.0, 800.0],
        }
    )
    curves = predict_survival(model, probe, 'customer_id', ['credit_score'])
    cum_low = curves[0].cumulative_default[-1]
    cum_high = curves[1].cumulative_default[-1]
    assert cum_low > cum_high


def test_survival_to_dataframe_long_format() -> None:
    df = _synthetic_survival_cohort(n=50)
    model = fit_discrete_hazard(
        df,
        'customer_id',
        'defaulted',
        'default_month',
        feature_cols=['credit_score'],
        horizon_months=12,
    )
    curves = predict_survival(model, df.head(3), 'customer_id', ['credit_score'])
    out = survival_to_dataframe(curves)
    assert {
        'customer_id',
        'month',
        'hazard',
        'survival',
        'cumulative_default',
    }.issubset(out.columns)
    assert len(out) == 3 * 12


def test_peak_hazard_month_identifies_max() -> None:
    curve = SurvivalCurve(
        customer_id='x',
        months=tuple(range(1, 13)),
        hazard=(0.01, 0.02, 0.05, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02, 0.02, 0.01, 0.01),
        survival=(
            0.99,
            0.97,
            0.92,
            0.83,
            0.76,
            0.71,
            0.68,
            0.66,
            0.65,
            0.63,
            0.63,
            0.62,
        ),
        cumulative_default=(
            0.01,
            0.03,
            0.08,
            0.17,
            0.24,
            0.29,
            0.32,
            0.34,
            0.35,
            0.37,
            0.37,
            0.38,
        ),
        expected_lifetime_months=8.4,
    )
    assert peak_hazard_month(curve) == 4
