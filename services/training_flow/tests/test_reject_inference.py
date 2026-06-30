"""Unit tests for reject inference (Phase D A8)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.reject_inference import (
    FuzzyParcelResult,
    HeckmanResult,
    fit_with_fuzzy_parcel,
    fuzzy_parcel,
    heckman_two_stage,
    predict_with_heckman,
    simulate_rejected_population,
)


def test_fuzzy_parcel_row_counts() -> None:
    """n_accept rows + 2*n_reject synthetic rows = total."""
    X_accept = np.random.rand(100, 3)
    y_accept = np.random.randint(0, 2, size=100)
    X_reject = np.random.rand(50, 3)
    p_def_reject = np.random.rand(50) * 0.3
    result = fuzzy_parcel(X_accept, y_accept, X_reject, p_def_reject)
    assert isinstance(result, FuzzyParcelResult)
    assert result.X_augmented.shape == (100 + 2 * 50, 3)
    assert len(result.y_augmented) == 100 + 2 * 50
    assert len(result.weights) == 100 + 2 * 50


def test_fuzzy_parcel_weight_sums_correct() -> None:
    """For each rejected applicant, weights on (defaulted=1, defaulted=0) sum to 1."""
    X_accept = np.random.rand(10, 2)
    y_accept = np.zeros(10, dtype=int)
    X_reject = np.random.rand(5, 2)
    p_def_reject = np.array([0.1, 0.2, 0.5, 0.8, 0.9])
    result = fuzzy_parcel(X_accept, y_accept, X_reject, p_def_reject)
    # Accepts get weight 1
    for i in range(10):
        assert result.weights[i] == 1.0
    # Rejected default rows get weight p_def_reject
    for i in range(5):
        assert abs(result.weights[10 + i] - p_def_reject[i]) < 1e-9
    # Rejected non-default rows get weight 1 - p_def_reject
    for i in range(5):
        assert abs(result.weights[15 + i] - (1.0 - p_def_reject[i])) < 1e-9


def test_fit_with_fuzzy_parcel_returns_classifier() -> None:
    """Fitting succeeds and returns a usable LogisticRegression."""
    rng = np.random.default_rng(seed=42)
    X_accept = rng.standard_normal((200, 3))
    y_accept = rng.binomial(1, 0.2, size=200)
    X_reject = rng.standard_normal((50, 3))
    p_def_reject = rng.uniform(0.1, 0.4, size=50)
    parcel = fuzzy_parcel(X_accept, y_accept, X_reject, p_def_reject)
    clf = fit_with_fuzzy_parcel(parcel)
    assert hasattr(clf, 'predict_proba')
    preds = clf.predict_proba(X_accept)
    assert preds.shape == (200, 2)


def test_heckman_two_stage_returns_two_models() -> None:
    """Heckman returns selection + outcome models with IMR augmented."""
    rng = np.random.default_rng(seed=42)
    n = 500
    X_sel = rng.standard_normal((n, 3))
    accepted = rng.binomial(1, 0.6, size=n)
    X_out = rng.standard_normal((n, 2))
    y_out = rng.binomial(1, 0.2, size=n)

    result = heckman_two_stage(
        X_sel,
        accepted,
        X_out,
        y_out,
        feature_names_stage1=['s1', 's2', 's3'],
        feature_names_stage2=['o1', 'o2'],
    )
    assert isinstance(result, HeckmanResult)
    assert 'inverse_mills_ratio' in result.feature_names_stage2
    assert result.n_observed == int(accepted.sum())
    assert result.n_total == n


def test_predict_with_heckman_outputs_probabilities() -> None:
    """Predictions are in [0, 1] after the two-stage pipeline."""
    rng = np.random.default_rng(seed=42)
    n = 300
    X_sel = rng.standard_normal((n, 3))
    accepted = (X_sel[:, 0] > -0.5).astype(int)  # ensure some accepted
    X_out = rng.standard_normal((n, 2))
    y_out = rng.binomial(1, 0.25, size=n)
    result = heckman_two_stage(X_sel, accepted, X_out, y_out)
    # Predict on a new "population" (could be the rejected applicants)
    preds = predict_with_heckman(result, X_sel, X_out)
    assert preds.shape == (n,)
    assert (preds >= 0.0).all() and (preds <= 1.0).all()


def test_simulate_rejected_population_splits_by_threshold() -> None:
    df = pd.DataFrame(
        {
            'customer_id': [f'c{i}' for i in range(100)],
            'true_p_accept_cli': np.linspace(0.0, 1.0, 100),
        }
    )
    accepts, rejects = simulate_rejected_population(df, p_reject_threshold=0.5)
    assert len(accepts) + len(rejects) == 100
    assert all(accepts['true_p_accept_cli'] >= 0.5)
    assert all(rejects['true_p_accept_cli'] < 0.5)


def test_fuzzy_parcel_zero_rejects_returns_accepts_only() -> None:
    """Edge case: no rejected applicants → augmented = accepts."""
    X_accept = np.random.rand(50, 2)
    y_accept = np.random.randint(0, 2, size=50)
    X_reject = np.zeros((0, 2))
    p_def_reject = np.zeros(0)
    result = fuzzy_parcel(X_accept, y_accept, X_reject, p_def_reject)
    assert result.X_augmented.shape == (50, 2)
    assert result.n_rejects == 0
