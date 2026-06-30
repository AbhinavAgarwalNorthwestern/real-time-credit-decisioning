"""Day-6: Off-policy evaluation harness.

Three estimators for "what would policy π_new have earned, given logged
decisions from policy π_old?":

  - IPS (Inverse Propensity Score): unbiased, high variance
  - SNIPS (Self-Normalized IPS): bias for variance — bounded weights
  - DR (Doubly Robust): outcome model + IPS; unbiased if EITHER the
    outcome model OR the propensity model is correctly specified

All three return a bootstrap CI (default 1000 resamples). Day-6's PR
includes a leaderboard of (policy, estimate, 95% CI) so reviewers can
gate alias swaps against the lower CI bound, not the point estimate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class OPEResult:
    estimator: str
    estimate: float
    ci_low_95: float
    ci_high_95: float
    n_logged: int


def _bootstrap_ci(
    values: np.ndarray, n_boot: int = 1000, rng: np.random.Generator | None = None
) -> tuple[float, float]:
    rng = rng or np.random.default_rng(seed=42)
    samples = rng.choice(values, size=(n_boot, len(values)), replace=True)
    means = samples.mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def ips(
    logged_action: np.ndarray,  # what π_old chose, shape (N,)
    logged_propensity: np.ndarray,  # P(action | x, π_old), shape (N,)
    logged_reward: np.ndarray,  # realized reward, shape (N,)
    new_policy_action: np.ndarray,  # what π_new would have chosen, shape (N,)
    new_policy_propensity: np.ndarray
    | None = None,  # P(action|x,π_new); =1 for deterministic
) -> OPEResult:
    """IPS estimator: E[r] under new policy."""
    match = (logged_action == new_policy_action).astype(float)
    weights = np.where(logged_propensity > 0, match / logged_propensity, 0.0)
    if new_policy_propensity is not None:
        weights *= new_policy_propensity
    contribs = weights * logged_reward
    estimate = float(contribs.mean())
    lo, hi = _bootstrap_ci(contribs)
    return OPEResult('IPS', estimate, lo, hi, len(logged_action))


def snips(
    logged_action: np.ndarray,
    logged_propensity: np.ndarray,
    logged_reward: np.ndarray,
    new_policy_action: np.ndarray,
    new_policy_propensity: np.ndarray | None = None,
) -> OPEResult:
    """Self-normalized IPS — bias for variance via weight self-normalization."""
    match = (logged_action == new_policy_action).astype(float)
    weights = np.where(logged_propensity > 0, match / logged_propensity, 0.0)
    if new_policy_propensity is not None:
        weights *= new_policy_propensity
    if weights.sum() == 0:
        return OPEResult('SNIPS', 0.0, 0.0, 0.0, len(logged_action))
    estimate = float((weights * logged_reward).sum() / weights.sum())
    # Bootstrap by resampling pairs and recomputing normalization.
    n = len(logged_action)
    rng = np.random.default_rng(seed=42)
    boot_estimates = []
    for _ in range(1000):
        idx = rng.integers(0, n, size=n)
        w = weights[idx]
        if w.sum() == 0:
            continue
        boot_estimates.append((w * logged_reward[idx]).sum() / w.sum())
    if boot_estimates:
        lo, hi = np.percentile(boot_estimates, [2.5, 97.5])
    else:
        lo = hi = estimate
    return OPEResult('SNIPS', estimate, float(lo), float(hi), n)


def doubly_robust(
    logged_action: np.ndarray,
    logged_propensity: np.ndarray,
    logged_reward: np.ndarray,
    new_policy_action: np.ndarray,
    outcome_model_pred_for_chosen: np.ndarray,  # μ̂(x, a*) per row
    outcome_model_pred_for_logged: np.ndarray,  # μ̂(x, a_logged) per row
    new_policy_propensity: np.ndarray | None = None,
) -> OPEResult:
    """Doubly-robust: μ̂(x, π_new(x)) + (r - μ̂(x, a_log)) * w(x, a_log).

    Consistent if EITHER the outcome model μ̂ OR the propensity model is
    correctly specified — the headline OPE choice for production.
    """
    match = (logged_action == new_policy_action).astype(float)
    w = np.where(logged_propensity > 0, match / logged_propensity, 0.0)
    if new_policy_propensity is not None:
        w *= new_policy_propensity

    residuals = logged_reward - outcome_model_pred_for_logged
    contribs = outcome_model_pred_for_chosen + w * residuals
    estimate = float(contribs.mean())
    lo, hi = _bootstrap_ci(contribs)
    return OPEResult('DR', estimate, lo, hi, len(logged_action))


def evaluate_policy_vs_logged(
    logged_action: np.ndarray,
    logged_propensity: np.ndarray,
    logged_reward: np.ndarray,
    new_policy_action: np.ndarray,
    outcome_model_pred_for_chosen: np.ndarray | None = None,
    outcome_model_pred_for_logged: np.ndarray | None = None,
) -> list[OPEResult]:
    """One-stop estimator suite. Returns IPS, SNIPS, and (if outcome
    model is provided) DR."""
    out = [
        ips(logged_action, logged_propensity, logged_reward, new_policy_action),
        snips(logged_action, logged_propensity, logged_reward, new_policy_action),
    ]
    if (
        outcome_model_pred_for_chosen is not None
        and outcome_model_pred_for_logged is not None
    ):
        out.append(
            doubly_robust(
                logged_action,
                logged_propensity,
                logged_reward,
                new_policy_action,
                outcome_model_pred_for_chosen,
                outcome_model_pred_for_logged,
            )
        )
    for r in out:
        log.info(
            'ope_result',
            **{
                k: getattr(r, k)
                for k in (
                    'estimator',
                    'estimate',
                    'ci_low_95',
                    'ci_high_95',
                    'n_logged',
                )
            },
        )
    return out
