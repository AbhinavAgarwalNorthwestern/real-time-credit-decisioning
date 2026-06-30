"""D2-3 — Baselines.

Reference policies the neural T-learner must beat to earn its keep:
  1. Always-offer constant policy
  2. Never-offer constant policy
  3. Random 50/50 policy
  4. Logistic-regression T-learner (LINEAR baseline — most important)

Each baseline produces a per-row predicted action (0 or 1) and the
simulated profit if that action were taken (using the same
label_simulator semantics for fairness).

Reported metrics per baseline (matching the neural model's reports
for apples-to-apples comparison):
  - Mean simulated profit per decision
  - Kendall τ between predicted uplift (where defined) and true uplift
  - % of rows where the model agrees with the optimal action

Extensible: add a new baseline by implementing the Baseline protocol
and appending to `make_baselines()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from . import get_logger
from .label_simulator import (
    CustomerParams,
    RowContext,
    expected_profit_if_treated,
)

log = get_logger(__name__)


class Baseline(Protocol):
    name: str

    def predict_action(self, features: pd.DataFrame) -> np.ndarray:
        """Returns a (n,) array of 0/1 actions."""
        ...

    def predict_uplift(self, features: pd.DataFrame) -> np.ndarray | None:
        """Returns a (n,) array of predicted uplift, or None if undefined
        (constant/random policies don't produce uplift estimates)."""
        ...


@dataclass(frozen=True, slots=True)
class AlwaysOfferPolicy:
    name: str = 'always_offer'

    def predict_action(self, features: pd.DataFrame) -> np.ndarray:
        return np.ones(len(features), dtype=int)

    def predict_uplift(self, features: pd.DataFrame) -> np.ndarray | None:
        return None


@dataclass(frozen=True, slots=True)
class NeverOfferPolicy:
    name: str = 'never_offer'

    def predict_action(self, features: pd.DataFrame) -> np.ndarray:
        return np.zeros(len(features), dtype=int)

    def predict_uplift(self, features: pd.DataFrame) -> np.ndarray | None:
        return None


@dataclass(frozen=True, slots=True)
class RandomPolicy:
    seed: int = 42
    name: str = 'random_50_50'

    def predict_action(self, features: pd.DataFrame) -> np.ndarray:
        rng = np.random.default_rng(self.seed)
        return rng.binomial(1, 0.5, size=len(features)).astype(int)

    def predict_uplift(self, features: pd.DataFrame) -> np.ndarray | None:
        return None


class LogisticTLearner:
    """Linear two-arm T-learner: logistic regression per arm; uplift =
    P(profit > 0 | T=1) - P(profit > 0 | T=0).

    Simplification: outcome regression heads (Y is continuous profit)
    use linear regression, not logistic — but for the headline "is
    neural needed?" comparison, what matters is whether a linear
    architecture can recover the signal. If linear T-learner ≈ neural,
    we ship linear.
    """

    name = 'logistic_t_learner'

    def __init__(self) -> None:
        self._mu_0 = None  # control-arm regressor
        self._mu_1 = None  # treated-arm regressor
        self._feature_cols: list[str] = []

    def fit(self, df: pd.DataFrame, feature_cols: list[str]) -> None:
        from sklearn.linear_model import LinearRegression

        self._feature_cols = feature_cols
        sub = df.dropna(subset=feature_cols + ['T', 'profit']).copy()

        control = sub[sub['T'] == 0]
        treated = sub[sub['T'] == 1]
        if len(control) < 10 or len(treated) < 10:
            raise ValueError('insufficient data per arm for T-learner')

        self._mu_0 = LinearRegression().fit(
            control[feature_cols].to_numpy(), control['profit'].to_numpy()
        )
        self._mu_1 = LinearRegression().fit(
            treated[feature_cols].to_numpy(), treated['profit'].to_numpy()
        )
        log.info(
            'logistic_t_learner_fit', n_control=len(control), n_treated=len(treated)
        )

    def predict_uplift(self, features: pd.DataFrame) -> np.ndarray:
        assert self._mu_0 is not None and self._mu_1 is not None, (
            'fit() must be called first'
        )
        X = features[self._feature_cols].fillna(0.0).to_numpy()
        y1 = self._mu_1.predict(X)
        y0 = self._mu_0.predict(X)
        return np.asarray(y1 - y0, dtype=float)

    def predict_action(self, features: pd.DataFrame) -> np.ndarray:
        uplift = self.predict_uplift(features)
        return (uplift > 0).astype(int)


@dataclass(frozen=True, slots=True)
class BaselineReport:
    name: str
    mean_simulated_profit: float
    optimal_agreement_rate: float
    kendall_tau_vs_true_uplift: float | None


def _true_uplift_per_row(row: pd.Series, cohort: dict[str, CustomerParams]) -> float:
    """Analytical true uplift = E[profit | T=1] - 0 (control baseline)."""
    cust = cohort.get(str(row['customer_id']))
    if cust is None:
        return 0.0
    ctx = RowContext(
        utilization=float(row.get('utilization', 0.5) or 0.5),
        velocity_24h=float(row.get('velocity_24h', 0.0) or 0.0),
        paydown_rate_30d=float(row.get('paydown_rate_30d', 0.5) or 0.5),
    )
    return expected_profit_if_treated(cust, ctx)


def evaluate(
    policy: Baseline,
    df: pd.DataFrame,
    cohort: dict[str, CustomerParams],
) -> BaselineReport:
    """Per-row: take policy's action, look up the analytical profit
    under that action, and aggregate."""
    from scipy.stats import kendalltau

    actions = policy.predict_action(df)
    true_uplifts = np.array(
        [_true_uplift_per_row(r, cohort) for _, r in df.iterrows()],
        dtype=float,
    )
    # Profit under the chosen action: T=1 → true uplift; T=0 → 0
    realized = np.where(actions == 1, true_uplifts, 0.0)

    optimal_actions = (true_uplifts > 0).astype(int)
    agreement = float((actions == optimal_actions).mean())

    tau: float | None = None
    predicted_uplift = policy.predict_uplift(df)
    if predicted_uplift is not None and len(predicted_uplift) > 1:
        tau_stat, _ = kendalltau(predicted_uplift, true_uplifts)
        if not np.isnan(tau_stat):
            tau = float(tau_stat)

    return BaselineReport(
        name=policy.name,
        mean_simulated_profit=float(realized.mean()),
        optimal_agreement_rate=agreement,
        kendall_tau_vs_true_uplift=tau,
    )


def make_baselines(df: pd.DataFrame, feature_cols: list[str]) -> list[Baseline]:
    """Construct all baselines; the linear T-learner is fitted here."""
    lin = LogisticTLearner()
    lin.fit(df, feature_cols)
    return [
        AlwaysOfferPolicy(),
        NeverOfferPolicy(),
        RandomPolicy(seed=42),
        lin,
    ]
