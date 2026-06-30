"""Bayesian hierarchical PD model — Phase H Item B10.

Standard credit-risk modeling produces a single point estimate of PD per
customer. **Bayesian hierarchical modeling** produces a full posterior
distribution + properly accounts for nested structure: a customer is in a
segment, segments are in a portfolio, portfolios are in an economic regime.
Pooling across levels gives:

1. **Calibrated uncertainty per prediction** — not just "PD = 0.05" but
   "PD = 0.05 ± 0.02 (95% credible interval)". Real banks use this for capital
   reserves: capital = EL + UL × multiplier, where UL is derived from posterior.
2. **Partial pooling across segments** — segments with few defaults borrow
   strength from related segments instead of producing wildly miscalibrated
   estimates from low-N data.
3. **Posterior predictive checks** — sample from the fit, simulate cohorts,
   compare against held-out actuals. Goodness-of-fit test that goes beyond
   point-AUC.
4. **Portfolio-level uncertainty quantification** — sum per-customer EL
   distributions to get portfolio-level loss distribution. CCAR / DFAST
   stress tests can use the tails directly.

The hierarchy: `customer ~ segment ~ portfolio`. We fit a logistic regression
with random effects on segment (intercept varies per segment, drawn from
a portfolio-level prior).

Why this is in the roadmap and not the production critical path: Bayesian
inference is computationally expensive (MCMC ~minutes per fit) and the
point-estimate model already handles most decisions well. The value here
is portfolio-level UQ and segments-with-few-defaults handling — both
quarterly-reporting concerns, not per-request serving.

Tries to use PyMC; falls back to a numpy/sklearn approximation if PyMC
isn't installed (the module is still importable + tested without PyMC).

References:
- Gelman et al., "Bayesian Data Analysis" §15 (hierarchical models)
- Salvatier et al. (2016), PyMC3 (and PyMC v5 successor)
- Basel II IRB Foundation: Bayesian PD methodologies

Cloud-agnostic: pure Python + optional PyMC.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    import arviz as az  # noqa: F401  (used by .summary())
    import pymc as pm

    _HAS_PYMC = True
except ImportError:
    _HAS_PYMC = False


@dataclass(frozen=True, slots=True)
class HierarchicalPDPosterior:
    """Posterior samples + summary for a fitted Bayesian PD model."""

    n_segments: int
    n_features: int
    feature_names: tuple[str, ...]
    n_samples: int
    # Per-segment intercept posteriors (n_samples × n_segments)
    segment_intercepts: np.ndarray
    # Shared feature coefficients (n_samples × n_features)
    feature_coefficients: np.ndarray
    fit_backend: str  # 'pymc' or 'numpy_fallback'


def fit_hierarchical_pd(
    df: pd.DataFrame,
    feature_cols: list[str],
    segment_col: str,
    target_col: str,
    n_samples: int = 1000,
    n_tune: int = 500,
    random_seed: int = 42,
) -> HierarchicalPDPosterior:
    """Fit a hierarchical Bayesian logistic regression for PD.

    Hierarchy:
        target_i ~ Bernoulli(p_i)
        logit(p_i) = α_{segment(i)} + β · x_i
        α_s ~ Normal(μ_α, σ_α)            (segment-level random intercepts)
        β   ~ Normal(0, 1) per feature    (shared coefficients)
        μ_α ~ Normal(0, 5)
        σ_α ~ HalfNormal(1)

    When PyMC isn't installed, falls back to a numpy MAP estimator that
    returns a degenerate "posterior" with 1 sample (mean only). The interface
    is identical so downstream code works in both cases.
    """
    X = df[feature_cols].to_numpy(dtype=float)
    y = df[target_col].to_numpy(dtype=int)
    seg = df[segment_col].to_numpy(dtype=int)
    n_segments = int(seg.max()) + 1

    if not _HAS_PYMC:
        return _fit_numpy_fallback(
            X,
            y,
            seg,
            feature_cols,
            n_segments,
            random_seed,
        )

    with pm.Model() as _:
        mu_alpha = pm.Normal('mu_alpha', mu=0, sigma=5)
        sigma_alpha = pm.HalfNormal('sigma_alpha', sigma=1)
        alpha = pm.Normal('alpha', mu=mu_alpha, sigma=sigma_alpha, shape=n_segments)
        beta = pm.Normal('beta', mu=0, sigma=1, shape=len(feature_cols))

        eta = alpha[seg] + pm.math.dot(X, beta)
        p = pm.Deterministic('p', pm.math.sigmoid(eta))
        pm.Bernoulli('obs', p=p, observed=y)

        trace = pm.sample(
            draws=n_samples,
            tune=n_tune,
            chains=2,
            random_seed=random_seed,
            progressbar=False,
            return_inferencedata=True,
        )

    # Collapse chains × draws → (n_samples * n_chains, ...) for downstream use
    alpha_samples = trace.posterior['alpha'].values.reshape(-1, n_segments)
    beta_samples = trace.posterior['beta'].values.reshape(-1, len(feature_cols))

    return HierarchicalPDPosterior(
        n_segments=n_segments,
        n_features=len(feature_cols),
        feature_names=tuple(feature_cols),
        n_samples=alpha_samples.shape[0],
        segment_intercepts=alpha_samples,
        feature_coefficients=beta_samples,
        fit_backend='pymc',
    )


def _fit_numpy_fallback(
    X: np.ndarray,
    y: np.ndarray,
    seg: np.ndarray,
    feature_cols: list[str],
    n_segments: int,
    random_seed: int,
) -> HierarchicalPDPosterior:
    """Fast MAP fallback when PyMC isn't installed.

    Fits a sklearn LogisticRegression with segment as a one-hot feature
    block + the actual features. Returns single point estimates packaged as
    a degenerate "posterior" (n_samples=1). Useful for CI/test environments
    that can't install PyMC; production should always use PyMC.
    """
    from sklearn.linear_model import LogisticRegression

    # One-hot encode segment
    seg_oh = np.zeros((len(y), n_segments), dtype=float)
    seg_oh[np.arange(len(y)), seg] = 1.0
    X_full = np.hstack([seg_oh, X])

    clf = LogisticRegression(max_iter=500, random_state=random_seed, solver='lbfgs')
    clf.fit(X_full, y)
    coefs = clf.coef_.ravel()
    segment_intercepts_mean = coefs[:n_segments]
    feature_coefs_mean = coefs[n_segments:]
    intercept = float(clf.intercept_[0])
    segment_intercepts_mean = segment_intercepts_mean + intercept

    return HierarchicalPDPosterior(
        n_segments=n_segments,
        n_features=len(feature_cols),
        feature_names=tuple(feature_cols),
        n_samples=1,
        segment_intercepts=segment_intercepts_mean.reshape(1, -1),
        feature_coefficients=feature_coefs_mean.reshape(1, -1),
        fit_backend='numpy_fallback',
    )


def predict_pd_posterior(
    posterior: HierarchicalPDPosterior,
    df: pd.DataFrame,
    feature_cols: list[str],
    segment_col: str,
) -> np.ndarray:
    """Predict PD posterior samples per row.

    Returns array of shape (n_samples, n_rows). Each row in the output
    represents one posterior draw of PD for each input row.
    """
    X = df[feature_cols].to_numpy(dtype=float)
    seg = df[segment_col].to_numpy(dtype=int)

    eta = (
        posterior.segment_intercepts[:, seg]  # (n_samples, n_rows)
        + posterior.feature_coefficients @ X.T  # (n_samples, n_rows)
    )
    # Logistic
    return 1.0 / (1.0 + np.exp(-eta))


@dataclass(frozen=True, slots=True)
class CredibleInterval:
    """Posterior summary for one prediction."""

    customer_id: str
    median: float
    lower_95: float
    upper_95: float
    posterior_std: float


def credible_intervals(
    posterior_predictions: np.ndarray,
    customer_ids: list[str],
) -> list[CredibleInterval]:
    """Per-customer 95% credible intervals from posterior PD samples."""
    medians = np.median(posterior_predictions, axis=0)
    lowers = np.quantile(posterior_predictions, 0.025, axis=0)
    uppers = np.quantile(posterior_predictions, 0.975, axis=0)
    stds = np.std(posterior_predictions, axis=0)
    return [
        CredibleInterval(
            customer_id=customer_ids[i],
            median=float(medians[i]),
            lower_95=float(lowers[i]),
            upper_95=float(uppers[i]),
            posterior_std=float(stds[i]),
        )
        for i in range(len(customer_ids))
    ]


def portfolio_loss_distribution(
    posterior_pd_samples: np.ndarray,
    lgd_per_customer: np.ndarray,
    ead_per_customer: np.ndarray,
) -> np.ndarray:
    """Aggregate per-customer PD posterior into portfolio loss distribution.

    Returns array of shape (n_samples,) — each entry is the portfolio
    expected loss under one posterior draw. This is what regulators want
    for capital reserves: 99.9th percentile of this distribution is the
    "unexpected loss + expected loss" allocation.
    """
    el_per_customer_per_sample = (
        posterior_pd_samples * lgd_per_customer * ead_per_customer
    )
    return np.sum(el_per_customer_per_sample, axis=1)
