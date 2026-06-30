"""Monotonic-constrained Gradient Boosted Model — Phase B Item S5.

Real credit models are *required* to be monotonic in certain features for
regulatory acceptance:

- Higher `credit_score` → MUST yield lower predicted PD
- Higher `prev_delinquency_count` → MUST yield higher predicted PD
- Higher `account_tenure_months` → SHOULD yield lower predicted PD
- Higher `annual_income` → MUST yield lower predicted PD (capacity to pay)

The unconstrained T-learner in `model.py` does NOT enforce these. It can
(and often does) produce locally non-monotonic predictions because the
neural net fits noise — a customer with credit_score=820 might get a
HIGHER PD than a customer with credit_score=780, all else equal. This
fails fair-lending review and confuses operations staff who can't
explain it to regulators.

This module wraps `sklearn.ensemble.HistGradientBoostingClassifier`
(LightGBM-like) which natively supports `monotonic_cst`. The constraint
direction per feature must agree with the §4/§8 monotonicity preconditions
in `notebooks/01_customer_eda.ipynb` — applying a constraint to a
non-monotonic feature would HARM fit. So this module also exposes a helper
to validate the preconditions before training.

Industry references:
- Basel III IRB: monotonic dependence on risk drivers is implicit
- Equifax / Experian scoring: explicit monotonicity in score → PD
- sklearn user guide §1.11.5: monotonic constraints in HistGradientBoosting
- "Fair Lending Compliance" (FFIEC): non-monotonic decisions need explicit explanation

Used as the model variant in `ml_evaluation.ipynb` for post-Step-12.6
champion-challenger comparison (T-learner vs monotonic GBM). Whichever
performs better on OPE wins, but the monotonic GBM has the regulatory
edge regardless.

Cloud-agnostic: pure numpy + pandas + sklearn. No cluster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score


class MonotonicDirection(IntEnum):
    """sklearn's monotonic_cst direction convention."""

    DECREASING = -1  # feature ↑ → PD ↓ (e.g., credit_score, income, tenure)
    NONE = 0  # no constraint (use for non-monotonic or transient features)
    INCREASING = +1  # feature ↑ → PD ↑ (e.g., prev_delinquency_count, utilization)


# Default constraint set for the 26-feature model after the customer-attribute
# rollout. Applied to features whose §4/§8 preconditions are STRONGLY monotonic.
# Transient behavioral features (velocity, total_spend) are intentionally NOT
# constrained because their relationship to PD is non-monotonic (a quick burst
# may indicate either fraud OR normal life events).
DEFAULT_CREDIT_CONSTRAINTS: dict[str, MonotonicDirection] = {
    # Customer-level attributes (strong monotonic per DGP design)
    'credit_score': MonotonicDirection.DECREASING,
    'annual_income': MonotonicDirection.DECREASING,
    'account_tenure_months': MonotonicDirection.DECREASING,
    'prev_delinquency_count': MonotonicDirection.INCREASING,
    # Behavioral utilization (capacity-pressure signal)
    'utilization': MonotonicDirection.INCREASING,
    'avg_utilization_30d': MonotonicDirection.INCREASING,
    # Behavioral risk signals
    'pct_cash_advance_30d': MonotonicDirection.INCREASING,
    'paydown_rate_30d': MonotonicDirection.DECREASING,
    # n_products: weak signal, leave unconstrained
    # All velocity/spend features: non-monotonic, leave unconstrained
}


@dataclass(frozen=True, slots=True)
class MonotonicGBMConfig:
    """Hyperparameters for the monotonic GBM trainer.

    Defaults are conservative — sized for the ~30k-row training sets the
    pipeline produces, not Capital-One scale. Tune via Optuna in production.
    """

    max_iter: int = 200
    learning_rate: float = 0.05
    max_depth: int | None = 6
    min_samples_leaf: int = 20
    l2_regularization: float = 1.0
    early_stopping: bool = True
    validation_fraction: float = 0.15
    n_iter_no_change: int = 20
    random_state: int = 42


@dataclass(frozen=True, slots=True)
class MonotonicGBMResult:
    """Fitted model + metadata."""

    model: HistGradientBoostingClassifier
    feature_names: tuple[str, ...]
    monotonic_cst: tuple[int, ...]
    train_auc: float
    n_train: int
    n_features: int
    constraints_applied: dict[str, str] = field(default_factory=dict)


def build_constraint_vector(
    feature_names: list[str],
    constraints_map: dict[str, MonotonicDirection] | None = None,
) -> tuple[int, ...]:
    """Build the constraint vector for sklearn from a feature-name → direction map.

    Features not present in `constraints_map` get NONE (0). Order MUST match
    the columns of the X matrix passed at fit time.
    """
    cmap = (
        constraints_map if constraints_map is not None else DEFAULT_CREDIT_CONSTRAINTS
    )
    out: list[int] = []
    for name in feature_names:
        direction = cmap.get(name, MonotonicDirection.NONE)
        out.append(int(direction))
    return tuple(out)


def validate_preconditions(
    df: pd.DataFrame,
    target: pd.Series,
    constraints_map: dict[str, MonotonicDirection],
    n_bins: int = 10,
    spearman_threshold: float = 0.5,
) -> dict[str, str]:
    """Check that proposed monotonic constraints match the empirical data.

    For each feature with a non-NONE direction, bin into deciles and compute
    the Spearman rank correlation between bucket-mean(feature) and bucket-mean(target).
    Flag violations.

    Returns a dict of {feature: status} where status is 'ok', 'wrong_direction', or 'weak'.
    """
    statuses: dict[str, str] = {}
    for feature, direction in constraints_map.items():
        if direction == MonotonicDirection.NONE:
            continue
        if feature not in df.columns:
            statuses[feature] = 'missing'
            continue
        local = pd.DataFrame(
            {'x': df[feature].to_numpy(), 'y': target.to_numpy()}
        ).dropna()
        if local['x'].nunique() <= n_bins:
            local['bin'] = local['x']
        else:
            local['bin'] = pd.qcut(
                local['x'], q=n_bins, duplicates='drop', labels=False
            )
        bins = (
            local.groupby('bin')
            .agg(x_mean=('x', 'mean'), y_mean=('y', 'mean'))
            .reset_index()
        )
        if len(bins) < 3:
            statuses[feature] = 'too_few_bins'
            continue
        rho = float(bins[['x_mean', 'y_mean']].corr(method='spearman').iloc[0, 1])
        expected_sign = +1 if direction == MonotonicDirection.INCREASING else -1
        if abs(rho) < spearman_threshold:
            statuses[feature] = f'weak (rho={rho:+.3f})'
        elif np.sign(rho) != expected_sign:
            statuses[feature] = (
                f'wrong_direction (rho={rho:+.3f}, expected sign={expected_sign:+d})'
            )
        else:
            statuses[feature] = f'ok (rho={rho:+.3f})'
    return statuses


def train_monotonic_gbm(
    df: pd.DataFrame,
    target: pd.Series,
    feature_cols: list[str],
    constraints_map: dict[str, MonotonicDirection] | None = None,
    config: MonotonicGBMConfig | None = None,
) -> MonotonicGBMResult:
    """Fit a monotonic-constrained GBM for binary PD classification.

    Parameters
    ----------
    df
        Feature matrix DataFrame.
    target
        Binary {0, 1} default outcome aligned with `df`.
    feature_cols
        Ordered feature names; defines the column order in the X matrix.
    constraints_map
        Feature-name → MonotonicDirection. Defaults to DEFAULT_CREDIT_CONSTRAINTS.
    config
        Trainer hyperparameters.
    """
    cmap = (
        constraints_map if constraints_map is not None else DEFAULT_CREDIT_CONSTRAINTS
    )
    cfg = config if config is not None else MonotonicGBMConfig()

    X = df[feature_cols].to_numpy()
    y = target.to_numpy()
    cst = build_constraint_vector(feature_cols, cmap)

    model = HistGradientBoostingClassifier(
        max_iter=cfg.max_iter,
        learning_rate=cfg.learning_rate,
        max_depth=cfg.max_depth,
        min_samples_leaf=cfg.min_samples_leaf,
        l2_regularization=cfg.l2_regularization,
        early_stopping=cfg.early_stopping,
        validation_fraction=cfg.validation_fraction,
        n_iter_no_change=cfg.n_iter_no_change,
        monotonic_cst=cst,
        random_state=cfg.random_state,
    )
    model.fit(X, y)
    train_pred = model.predict_proba(X)[:, 1]
    train_auc = (
        float(roc_auc_score(y, train_pred)) if len(np.unique(y)) > 1 else float('nan')
    )

    constraints_applied = {
        feature_cols[i]: MonotonicDirection(cst[i]).name
        for i in range(len(feature_cols))
        if cst[i] != 0
    }

    return MonotonicGBMResult(
        model=model,
        feature_names=tuple(feature_cols),
        monotonic_cst=cst,
        train_auc=train_auc,
        n_train=len(X),
        n_features=len(feature_cols),
        constraints_applied=constraints_applied,
    )


def predict_pd(result: MonotonicGBMResult, df: pd.DataFrame) -> np.ndarray:
    """Predict probability-of-default for a new DataFrame using the fitted model."""
    X = df[list(result.feature_names)].to_numpy()
    proba = result.model.predict_proba(X)
    return np.asarray(proba[:, 1], dtype=float)
