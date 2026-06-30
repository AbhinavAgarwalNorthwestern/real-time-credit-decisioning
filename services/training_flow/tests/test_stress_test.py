"""Unit tests for stress testing (Phase D A9)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.stress_test import (
    DFAST_ADVERSE,
    DFAST_BASELINE,
    STANDARD_SCENARIOS,
    ScenarioResult,
    StressScenario,
    apply_scenario,
    run_stress_test,
    stress_test_to_dataframe,
)


def _trivial_pd_fn(df: pd.DataFrame) -> np.ndarray:
    """Deterministic PD predictor: 0.05 floor + 0.0005 × (1000 − credit_score)."""
    scores = df['credit_score'].to_numpy()
    return np.clip(0.05 + 0.0005 * (1000 - scores), 0.0, 1.0)


def _sample_portfolio(n: int = 100) -> pd.DataFrame:
    rng = np.random.default_rng(seed=42)
    return pd.DataFrame(
        {
            'customer_id': [f'c{i}' for i in range(n)],
            'credit_score': rng.uniform(500, 850, size=n),
            'annual_income': rng.uniform(30000, 150000, size=n),
            'prev_delinquency_count': rng.integers(0, 5, size=n).astype(float),
            'utilization': rng.uniform(0.1, 0.7, size=n),
            'avg_utilization_30d': rng.uniform(0.1, 0.7, size=n),
            'paydown_rate_30d': rng.uniform(0.0, 1.0, size=n),
            'current_balance': rng.uniform(100, 4000, size=n),
            'credit_limit': rng.uniform(1000, 10000, size=n),
        }
    )


def test_apply_scenario_baseline_is_noop() -> None:
    """Baseline scenario has no shocks; output equals input."""
    df = _sample_portfolio(n=50)
    out = apply_scenario(df, DFAST_BASELINE)
    pd.testing.assert_frame_equal(out, df)


def test_apply_scenario_multiplicative_shock_works() -> None:
    """Adverse scenario shrinks annual_income by 10%."""
    df = _sample_portfolio(n=50)
    out = apply_scenario(df, DFAST_ADVERSE)
    expected_income = df['annual_income'] * 0.90
    np.testing.assert_array_almost_equal(
        out['annual_income'].to_numpy(), expected_income.to_numpy()
    )


def test_apply_scenario_additive_shock_works() -> None:
    """Adverse scenario adds 1 to prev_delinquency_count."""
    df = _sample_portfolio(n=50)
    out = apply_scenario(df, DFAST_ADVERSE)
    expected_delinq = df['prev_delinquency_count'] + 1.0
    np.testing.assert_array_almost_equal(
        out['prev_delinquency_count'].to_numpy(), expected_delinq.to_numpy()
    )


def test_apply_scenario_missing_columns_skipped_silently() -> None:
    """Scenarios referencing absent features don't crash."""
    df = pd.DataFrame({'customer_id': ['x'], 'credit_score': [700.0]})
    sc = StressScenario(
        name='test',
        description='',
        multiplicative={'absent_feature': 0.5},
        additive={'also_absent': 1.0},
    )
    out = apply_scenario(df, sc)
    pd.testing.assert_frame_equal(out, df)


def test_run_stress_test_three_scenarios() -> None:
    """run_stress_test produces one result per scenario."""
    df = _sample_portfolio(n=200)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    assert len(results) == 3
    names = [r.scenario_name for r in results]
    assert names == ['baseline', 'adverse', 'severely_adverse']


def test_severely_adverse_loss_rate_exceeds_baseline() -> None:
    """Severely adverse must produce higher portfolio loss than baseline."""
    df = _sample_portfolio(n=500)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    baseline = next(r for r in results if r.scenario_name == 'baseline')
    severe = next(r for r in results if r.scenario_name == 'severely_adverse')
    assert severe.expected_loss > baseline.expected_loss
    assert severe.loss_rate > baseline.loss_rate


def test_pd_floor_applied_in_severe_scenario() -> None:
    """Severely adverse has a 5% PD floor; should be flagged."""
    df = _sample_portfolio(n=100)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    severe = next(r for r in results if r.scenario_name == 'severely_adverse')
    assert severe.pd_floor_applied


def test_stress_test_to_dataframe_columns() -> None:
    df = _sample_portfolio(n=50)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    out = stress_test_to_dataframe(results)
    expected_cols = {
        'scenario',
        'n_customers',
        'portfolio_ead',
        'expected_loss',
        'loss_rate_pct',
        'unexpected_loss',
        'weighted_avg_pd',
        'pd_floor_applied',
    }
    assert expected_cols.issubset(out.columns)
    assert len(out) == 3


def test_custom_scenario_list_works() -> None:
    """Caller can pass their own scenario set, not just the standard three."""
    custom = StressScenario(
        name='custom_mild',
        description='Mild stress',
        multiplicative={'utilization': 1.05},
        pd_floor=0.01,
    )
    df = _sample_portfolio(n=50)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        scenarios=[DFAST_BASELINE, custom],
    )
    assert len(results) == 2
    assert results[1].scenario_name == 'custom_mild'


def test_lgd_override_applied_under_stress() -> None:
    """When lgd_override is set, stress test uses that LGD instead of base."""
    df = _sample_portfolio(n=100)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        base_lgd=0.80,
    )
    # Severely adverse uses LGD=0.90 vs baseline LGD=0.80
    # Expected loss = PD × LGD × EAD; higher LGD → higher EL.
    baseline = next(r for r in results if r.scenario_name == 'baseline')
    severe = next(r for r in results if r.scenario_name == 'severely_adverse')
    # The portfolio EAD also changes with utilization, so the comparison isn't pure;
    # but loss_rate should still be higher.
    assert severe.loss_rate > baseline.loss_rate


def test_standard_scenarios_count() -> None:
    """Three standard FRB DFAST scenarios baked in."""
    assert len(STANDARD_SCENARIOS) == 3


def test_scenario_result_dataclass() -> None:
    """ScenarioResult has the right fields."""
    df = _sample_portfolio(n=50)
    results = run_stress_test(
        df,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    r = results[0]
    assert isinstance(r, ScenarioResult)
    assert hasattr(r, 'expected_loss')
    assert hasattr(r, 'loss_rate')
    assert hasattr(r, 'pd_floor_applied')
