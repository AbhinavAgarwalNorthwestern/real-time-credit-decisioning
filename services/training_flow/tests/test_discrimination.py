"""Unit tests for credit-discrimination metrics (Phase B Item S3).

Pure in-memory tests. No cluster, no cloud, no network.
"""

from __future__ import annotations

import numpy as np
from training_flow.discrimination import (
    DiscriminationResult,
    LorenzPoint,
    discrimination_summary,
    gini_coefficient,
    gini_interpretation,
    ks_interpretation,
    ks_statistic,
    lorenz_curve,
)


def test_gini_perfect_predictor() -> None:
    """A perfect ranking → Gini = 1.0."""
    y_true = np.array([0, 0, 0, 1, 1, 1])
    y_pred = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    assert gini_coefficient(y_true, y_pred) == 1.0


def test_gini_random_predictor() -> None:
    """A truly random predictor → Gini ~ 0."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    y_true = rng.binomial(1, 0.3, size=n).astype(int)
    y_pred = rng.uniform(0, 1, size=n)
    g = gini_coefficient(y_true, y_pred)
    assert abs(g) < 0.05, f'random predictor should give Gini~0, got {g}'


def test_gini_inverted_predictor() -> None:
    """An anti-correlated predictor → Gini = -1.0."""
    y_true = np.array([0, 0, 0, 1, 1, 1])
    y_pred = np.array([0.9, 0.8, 0.7, 0.3, 0.2, 0.1])  # inverted
    assert gini_coefficient(y_true, y_pred) == -1.0


def test_gini_requires_both_classes() -> None:
    """Gini is undefined when only one class is present."""
    y_true = np.array([1, 1, 1])
    y_pred = np.array([0.5, 0.6, 0.7])
    try:
        gini_coefficient(y_true, y_pred)
        raise AssertionError('should have raised')
    except ValueError:
        pass


def test_ks_perfect_separation() -> None:
    """Perfect separation → KS = 1.0."""
    y_true = np.array([0, 0, 0, 0, 1, 1, 1, 1])
    y_pred = np.array([0.1, 0.2, 0.3, 0.4, 0.6, 0.7, 0.8, 0.9])
    ks, _t = ks_statistic(y_true, y_pred)
    assert ks == 1.0


def test_ks_random_is_small() -> None:
    """Random predictor → KS small but not zero (sampling noise)."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    y_true = rng.binomial(1, 0.3, size=n).astype(int)
    y_pred = rng.uniform(0, 1, size=n)
    ks, _t = ks_statistic(y_true, y_pred)
    assert ks < 0.10, f'random predictor should give small KS, got {ks}'


def test_ks_threshold_in_range() -> None:
    """KS threshold must be a valid predicted value from the input."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    _ks, threshold = ks_statistic(y_true, y_pred)
    assert 0.0 <= threshold <= 1.0


def test_lorenz_curve_perfect_predictor_lifts_first() -> None:
    """A perfect predictor concentrates events in the riskiest deciles."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = (y_pred > 0.5).astype(int)  # perfect: events are exactly the high-PD ones
    lorenz = lorenz_curve(y_true, y_pred, n_bins=10)
    assert len(lorenz) == 10
    # First decile should already capture much more than 10% of events
    assert lorenz[0].cum_event_pct > 0.15


def test_lorenz_curve_random_is_diagonal() -> None:
    """Random predictor → Lorenz curve approximately diagonal (no lift)."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    y_true = rng.binomial(1, 0.3, size=n).astype(int)
    y_pred = rng.uniform(0, 1, size=n)
    lorenz = lorenz_curve(y_true, y_pred, n_bins=10)
    # First decile event capture should be close to 10% under randomness
    assert 0.06 < lorenz[0].cum_event_pct < 0.14


def test_lorenz_final_decile_captures_100pct() -> None:
    """Sum across all deciles = 100% of population AND 100% of events."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    lorenz = lorenz_curve(y_true, y_pred, n_bins=10)
    last = lorenz[-1]
    assert abs(last.cum_pop_pct - 1.0) < 1e-6
    assert abs(last.cum_event_pct - 1.0) < 1e-6


def test_lorenz_cumulative_pop_pct_monotonic() -> None:
    """cum_pop_pct must be monotonically increasing across deciles."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    lorenz = lorenz_curve(y_true, y_pred, n_bins=10)
    for i in range(len(lorenz) - 1):
        assert lorenz[i].cum_pop_pct <= lorenz[i + 1].cum_pop_pct


def test_lorenz_cumulative_event_pct_monotonic() -> None:
    """cum_event_pct must be monotonically increasing (can't lose events)."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    lorenz = lorenz_curve(y_true, y_pred, n_bins=10)
    for i in range(len(lorenz) - 1):
        assert lorenz[i].cum_event_pct <= lorenz[i + 1].cum_event_pct


def test_discrimination_summary_combines_everything() -> None:
    """discrimination_summary returns a complete DiscriminationResult."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    result = discrimination_summary(y_true, y_pred, n_bins=10)
    assert isinstance(result, DiscriminationResult)
    assert result.n_observations == n
    assert result.n_events == int(y_true.sum())
    assert 0.0 <= result.auc <= 1.0
    assert -1.0 <= result.gini <= 1.0
    assert 0.0 <= result.ks_statistic <= 1.0
    assert isinstance(result.lorenz_curve[0], LorenzPoint)
    assert abs(result.gini - (2 * result.auc - 1.0)) < 1e-9


def test_gini_interpretation_buckets() -> None:
    """Industry buckets: < 0.30 insufficient, 0.30-0.40 marginal, etc."""
    assert gini_interpretation(0.10) == 'insufficient for regulator-grade decisioning'
    assert gini_interpretation(0.35) == 'marginal'
    assert gini_interpretation(0.45) == 'good (deployable application PD)'
    assert gini_interpretation(0.60) == 'excellent'


def test_ks_interpretation_buckets() -> None:
    """Industry buckets: < 0.30 insufficient, 0.30-0.50 deployable, ≥ 0.50 excellent."""
    assert ks_interpretation(0.10) == 'insufficient'
    assert ks_interpretation(0.35) == 'deployable'
    assert ks_interpretation(0.60) == 'excellent'


def test_top_decile_capture_rate_matches_first_lorenz_point() -> None:
    """top_decile_capture_rate in DiscriminationResult == lorenz[0].cum_event_pct."""
    rng = np.random.default_rng(seed=42)
    n = 1500
    y_pred = rng.uniform(0, 1, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    result = discrimination_summary(y_true, y_pred, n_bins=10)
    assert result.top_decile_capture_rate == result.lorenz_curve[0].cum_event_pct
