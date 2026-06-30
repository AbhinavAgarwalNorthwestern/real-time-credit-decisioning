"""Unit tests for Bayesian hierarchical PD (Phase H B10).

PyMC is optional — when present, full MCMC sampling tests run. When absent,
the numpy fallback is exercised. Both backends share the same Posterior API.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.bayesian_pd import (
    CredibleInterval,
    HierarchicalPDPosterior,
    credible_intervals,
    fit_hierarchical_pd,
    portfolio_loss_distribution,
    predict_pd_posterior,
)


def _synthetic_pd_data(n: int = 300, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed=seed)
    n_segments = 4
    seg = rng.integers(0, n_segments, size=n)
    # Per-segment baseline default rate
    seg_baseline = np.array([0.02, 0.05, 0.10, 0.20])
    credit_score = rng.uniform(500, 850, size=n)
    income_z = rng.standard_normal(size=n)
    logit = (
        -np.log(1 / seg_baseline[seg] - 1)
        - 0.01 * (credit_score - 650)
        + 0.2 * income_z
    )
    p = 1 / (1 + np.exp(-logit))
    y = rng.binomial(1, p)
    return pd.DataFrame(
        {
            'customer_id': [f'c{i}' for i in range(n)],
            'segment_id': seg,
            'credit_score': credit_score,
            'income_z': income_z,
            'defaulted': y,
        }
    )


def test_fit_returns_posterior_object() -> None:
    df = _synthetic_pd_data(n=200)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=200,
        n_tune=100,
    )
    assert isinstance(posterior, HierarchicalPDPosterior)
    assert posterior.n_segments == 4
    assert posterior.n_features == 2
    assert posterior.feature_names == ('credit_score', 'income_z')
    assert posterior.n_samples >= 1
    assert posterior.fit_backend in ('pymc', 'numpy_fallback')


def test_predict_returns_n_samples_x_n_rows() -> None:
    df = _synthetic_pd_data(n=100)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=100,
        n_tune=50,
    )
    preds = predict_pd_posterior(
        posterior,
        df.head(10),
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
    )
    assert preds.shape == (posterior.n_samples, 10)
    # All in [0, 1]
    assert (preds >= 0).all() and (preds <= 1).all()


def test_credible_intervals_shape_and_ordering() -> None:
    df = _synthetic_pd_data(n=50)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=200,
        n_tune=50,
    )
    preds = predict_pd_posterior(
        posterior,
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
    )
    intervals = credible_intervals(preds, df['customer_id'].tolist())
    assert len(intervals) == 50
    for ci in intervals:
        assert isinstance(ci, CredibleInterval)
        if posterior.n_samples > 1:
            # With proper posterior samples, lower <= median <= upper
            assert ci.lower_95 <= ci.median <= ci.upper_95
            assert ci.posterior_std >= 0.0


def test_portfolio_loss_distribution_shape() -> None:
    """portfolio_loss_distribution aggregates per-row to portfolio per sample."""
    n_samples = 50
    n_customers = 20
    pd_samples = np.random.rand(n_samples, n_customers) * 0.2
    lgd = np.full(n_customers, 0.8)
    ead = np.full(n_customers, 1000.0)
    loss_dist = portfolio_loss_distribution(pd_samples, lgd, ead)
    assert loss_dist.shape == (n_samples,)
    # Each entry = sum_i (pd_i × lgd_i × ead_i) for that posterior draw
    for s in range(n_samples):
        expected = np.sum(pd_samples[s] * lgd * ead)
        assert abs(loss_dist[s] - expected) < 1e-9


def test_portfolio_loss_distribution_99_9_percentile() -> None:
    """Validates that the loss distribution can be summarized for capital reserves."""
    n_samples = 1000
    n_customers = 50
    pd_samples = np.random.beta(2, 20, size=(n_samples, n_customers))
    lgd = np.full(n_customers, 0.8)
    ead = np.full(n_customers, 1000.0)
    loss_dist = portfolio_loss_distribution(pd_samples, lgd, ead)
    p999 = float(np.quantile(loss_dist, 0.999))
    p50 = float(np.quantile(loss_dist, 0.50))
    # 99.9th percentile should be higher than median — by definition of tail
    assert p999 > p50


def test_segment_intercepts_separate_per_segment() -> None:
    """The fitted posterior has one intercept per segment (not shared)."""
    df = _synthetic_pd_data(n=200)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=100,
        n_tune=50,
    )
    assert posterior.segment_intercepts.shape[1] == posterior.n_segments


def test_feature_coefficients_shape() -> None:
    """One coefficient per feature shared across segments (partial pooling)."""
    df = _synthetic_pd_data(n=100)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score', 'income_z'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=100,
        n_tune=50,
    )
    assert posterior.feature_coefficients.shape[1] == posterior.n_features


def test_numpy_fallback_still_works_without_pymc() -> None:
    """Even if PyMC is unavailable, the module fits and returns a posterior."""
    df = _synthetic_pd_data(n=80)
    posterior = fit_hierarchical_pd(
        df,
        feature_cols=['credit_score'],
        segment_col='segment_id',
        target_col='defaulted',
        n_samples=50,
        n_tune=25,
    )
    # In numpy_fallback mode, n_samples=1; in pymc mode, full
    assert posterior.fit_backend in ('pymc', 'numpy_fallback')
    preds = predict_pd_posterior(
        posterior,
        df.head(5),
        feature_cols=['credit_score'],
        segment_col='segment_id',
    )
    assert preds.shape[1] == 5
