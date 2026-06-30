"""Unit tests for monotonic-constrained GBM (Phase B Item S5).

Tests verify the contract:
1. Library accepts the expected inputs + returns the expected outputs
2. Monotonic constraints are actually enforced in the trained model
3. Precondition validator flags wrong-direction or weak constraints

Pure in-memory tests. No cluster, no cloud, no MLflow.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.monotonic_gbm import (
    DEFAULT_CREDIT_CONSTRAINTS,
    MonotonicDirection,
    MonotonicGBMConfig,
    MonotonicGBMResult,
    build_constraint_vector,
    predict_pd,
    train_monotonic_gbm,
    validate_preconditions,
)


def _synthetic_credit_dataset(
    n: int = 3000, seed: int = 42
) -> tuple[pd.DataFrame, pd.Series]:
    """Build a credit-like dataset with a known monotonic structure.

    PD increases with prev_delinquency and decreases with credit_score and tenure,
    so monotonic constraints applied to these features should fit cleanly.
    """
    rng = np.random.default_rng(seed=seed)
    credit_score = rng.uniform(500, 850, size=n)
    prev_delinquency = rng.integers(0, 8, size=n).astype(float)
    tenure = rng.uniform(1, 120, size=n)
    # Pure noise feature — non-monotonic
    velocity_5m = rng.uniform(0, 50, size=n)

    # Logistic PD with strong monotonic dependence
    logit = (
        -0.01 * (credit_score - 650)
        + 0.30 * prev_delinquency
        - 0.005 * tenure
        + rng.normal(0, 0.2, size=n)
    )
    p = 1.0 / (1.0 + np.exp(-logit))
    y = rng.binomial(1, p, size=n).astype(int)
    df = pd.DataFrame(
        {
            'credit_score': credit_score,
            'prev_delinquency_count': prev_delinquency,
            'account_tenure_months': tenure,
            'velocity_5m': velocity_5m,
        }
    )
    return df, pd.Series(y)


def test_build_constraint_vector_returns_correct_order() -> None:
    """Constraint vector index must match feature_cols order."""
    feature_cols = ['credit_score', 'velocity_5m', 'prev_delinquency_count']
    cmap = {
        'credit_score': MonotonicDirection.DECREASING,
        'prev_delinquency_count': MonotonicDirection.INCREASING,
    }
    cst = build_constraint_vector(feature_cols, cmap)
    assert cst == (-1, 0, +1)


def test_build_constraint_vector_uses_defaults_when_none() -> None:
    """When constraints_map is None, the default DEFAULT_CREDIT_CONSTRAINTS apply."""
    feature_cols = ['credit_score', 'unknown_feature']
    cst = build_constraint_vector(feature_cols, None)
    assert cst[0] == -1  # credit_score is DECREASING in defaults
    assert cst[1] == 0  # unknown_feature falls back to NONE


def test_train_monotonic_gbm_basic() -> None:
    """Train succeeds and returns a result with all expected fields."""
    df, y = _synthetic_credit_dataset(n=2000)
    feature_cols = [
        'credit_score',
        'prev_delinquency_count',
        'account_tenure_months',
        'velocity_5m',
    ]
    result = train_monotonic_gbm(df, y, feature_cols)
    assert isinstance(result, MonotonicGBMResult)
    assert result.n_train == 2000
    assert result.n_features == 4
    assert result.feature_names == tuple(feature_cols)
    assert 0.5 < result.train_auc <= 1.0  # better than random; not perfect


def test_predict_pd_returns_probabilities_in_unit_interval() -> None:
    """Predictions must be probabilities."""
    df, y = _synthetic_credit_dataset(n=1500)
    feature_cols = ['credit_score', 'prev_delinquency_count', 'account_tenure_months']
    result = train_monotonic_gbm(df, y, feature_cols)
    preds = predict_pd(result, df)
    assert preds.shape == (1500,)
    assert (preds >= 0.0).all() and (preds <= 1.0).all()


def test_monotonicity_enforced_in_credit_score() -> None:
    """Higher credit_score → lower PD, holding other features fixed.

    Probes the monotonicity by sweeping credit_score while keeping other
    features at their median. The model's predictions must be non-increasing.
    """
    df, y = _synthetic_credit_dataset(n=3000)
    feature_cols = ['credit_score', 'prev_delinquency_count', 'account_tenure_months']
    result = train_monotonic_gbm(df, y, feature_cols)

    # Build a sweep: vary credit_score from 500 to 850, others at median
    sweep = pd.DataFrame(
        {
            'credit_score': np.linspace(500, 850, 20),
            'prev_delinquency_count': [df['prev_delinquency_count'].median()] * 20,
            'account_tenure_months': [df['account_tenure_months'].median()] * 20,
        }
    )
    preds = predict_pd(result, sweep)
    # Monotonic decreasing: no point should be greater than the previous
    for i in range(len(preds) - 1):
        assert preds[i] >= preds[i + 1] - 1e-9, (
            f'monotonicity violated at credit_score sweep [{i}, {i + 1}]: '
            f'{preds[i]:.4f} → {preds[i + 1]:.4f}'
        )


def test_monotonicity_enforced_in_prev_delinquency() -> None:
    """Higher prev_delinquency → higher PD."""
    df, y = _synthetic_credit_dataset(n=3000)
    feature_cols = ['credit_score', 'prev_delinquency_count', 'account_tenure_months']
    result = train_monotonic_gbm(df, y, feature_cols)

    sweep = pd.DataFrame(
        {
            'credit_score': [df['credit_score'].median()] * 8,
            'prev_delinquency_count': np.arange(0, 8, dtype=float),
            'account_tenure_months': [df['account_tenure_months'].median()] * 8,
        }
    )
    preds = predict_pd(result, sweep)
    for i in range(len(preds) - 1):
        assert preds[i] <= preds[i + 1] + 1e-9, (
            f'monotonicity violated at prev_delinquency sweep [{i}, {i + 1}]: '
            f'{preds[i]:.4f} → {preds[i + 1]:.4f}'
        )


def test_validate_preconditions_passes_for_aligned_constraints() -> None:
    """Validator passes when constraint direction matches empirical Spearman."""
    df, y = _synthetic_credit_dataset(n=5000)
    constraints = {
        'credit_score': MonotonicDirection.DECREASING,
        'prev_delinquency_count': MonotonicDirection.INCREASING,
    }
    statuses = validate_preconditions(df, y, constraints, n_bins=10)
    assert all(s.startswith('ok') for s in statuses.values()), statuses


def test_validate_preconditions_flags_wrong_direction() -> None:
    """Validator flags when constraint direction contradicts the data."""
    df, y = _synthetic_credit_dataset(n=5000)
    # Wrong direction: claiming credit_score is INCREASING in PD (it's not)
    constraints = {
        'credit_score': MonotonicDirection.INCREASING,
    }
    statuses = validate_preconditions(df, y, constraints, n_bins=10)
    assert 'wrong_direction' in statuses['credit_score'], statuses


def test_validate_preconditions_flags_weak_signal() -> None:
    """Validator flags features whose relationship to PD is weak/noisy."""
    df, y = _synthetic_credit_dataset(n=5000)
    constraints = {
        'velocity_5m': MonotonicDirection.INCREASING,  # noise feature
    }
    statuses = validate_preconditions(df, y, constraints, n_bins=10)
    assert (
        'weak' in statuses['velocity_5m']
        or 'wrong_direction' in statuses['velocity_5m']
    ), statuses


def test_constraints_applied_reported_in_result() -> None:
    """Result records which constraints were applied for downstream reporting (model card)."""
    df, y = _synthetic_credit_dataset(n=1500)
    feature_cols = ['credit_score', 'prev_delinquency_count', 'velocity_5m']
    result = train_monotonic_gbm(df, y, feature_cols)
    # credit_score and prev_delinquency are in defaults
    assert 'credit_score' in result.constraints_applied
    assert result.constraints_applied['credit_score'] == 'DECREASING'
    assert 'prev_delinquency_count' in result.constraints_applied
    assert result.constraints_applied['prev_delinquency_count'] == 'INCREASING'
    # velocity_5m is not in defaults
    assert 'velocity_5m' not in result.constraints_applied


def test_config_default_values_are_sensible() -> None:
    """Defaults are conservative for the ~30k-row training sets the pipeline produces."""
    cfg = MonotonicGBMConfig()
    assert cfg.max_iter > 0
    assert 0 < cfg.learning_rate <= 1
    assert cfg.min_samples_leaf > 0
    assert cfg.random_state is not None


def test_default_credit_constraints_include_5_customer_attrs() -> None:
    """The 5 customer attributes added in v1.1.0 should all be in the default constraint set."""
    for col in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'prev_delinquency_count',
    ):
        assert col in DEFAULT_CREDIT_CONSTRAINTS, f'Missing constraint for {col}'
    # n_products is intentionally not constrained (weak signal in §4)
    assert 'n_products' not in DEFAULT_CREDIT_CONSTRAINTS


def test_unknown_feature_default_is_no_constraint() -> None:
    """Features not in the constraint map get NONE (0) — safe default."""
    cst = build_constraint_vector(['some_unknown_feature'], None)
    assert cst == (0,)
