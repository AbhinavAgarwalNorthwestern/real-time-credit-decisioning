"""Discrete-time hazard survival model — Phase D Item A7.

Real credit default is a TIME-TO-EVENT problem: a customer defaults at month
6, or month 14, or never within the observation window. Binary classification
ignores this: it treats "defaulted within 12 months" as a Bernoulli outcome
and discards the timing. That works for go/no-go decisions but loses signal
for:

- **Vintage analysis** — when within the first 24 months does default peak?
- **Pricing** — expected loss depends on when default occurs (PV discount)
- **Capital allocation** — Basel IRB requires PD over a 12-month horizon, but
  capital reserves are sized to peak-month default
- **Churn / attrition** — same math, different event

This module provides a **discrete-time hazard model** (one of the two standard
survival approaches; Cox PH is the other but requires `lifelines`). The
discrete-time hazard is just a logistic regression per time-bucket, predicting
"defaults THIS month given survived to last month". It's simpler than Cox PH
and reduces to a sklearn pipeline.

Output: per-customer, per-month hazard rates h(t) and survival S(t) curves.

Industry references:
- Allison (1995), "Survival Analysis Using SAS" §7 (discrete-time hazard)
- Singer & Willett (2003), "Applied Longitudinal Data Analysis" (the canonical text)
- Basel IRB: PD is a TTC (through-the-cycle) measure derived from these models

Used in `ml_evaluation.ipynb` as a sibling of the T-learner. Both models
predict default; survival exposes WHEN it happens.

Cloud-agnostic: pure numpy/pandas/sklearn. No cluster.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


@dataclass(frozen=True, slots=True)
class HazardModel:
    """Fitted discrete-time hazard model + metadata."""

    classifier: LogisticRegression
    feature_names: tuple[str, ...]
    n_time_buckets: int
    horizon_months: int


@dataclass(frozen=True, slots=True)
class SurvivalCurve:
    """Per-customer survival + hazard predictions over the horizon."""

    customer_id: str
    months: tuple[int, ...]  # 1, 2, ..., horizon_months
    hazard: tuple[float, ...]  # h(t) = P(default at t | survive to t-1)
    survival: tuple[float, ...]  # S(t) = product of (1 - h_i) for i ≤ t
    cumulative_default: tuple[float, ...]  # F(t) = 1 - S(t)
    expected_lifetime_months: float = 0.0


def _expand_to_person_periods(
    df: pd.DataFrame,
    customer_id_col: str,
    default_col: str,
    default_month_col: str,
    feature_cols: list[str],
    horizon_months: int = 24,
) -> pd.DataFrame:
    """Expand to person-period format: one row per (customer, month) at risk.

    Standard transform for discrete-time hazard. Right-censoring: customers
    who don't default by horizon contribute (horizon_months) periods of
    "survived". Defaulters contribute (default_month) periods, the last
    flagged as the event.
    """
    rows: list[dict] = []
    for _, row in df.iterrows():
        cust = row[customer_id_col]
        defaulted = bool(row[default_col])
        default_t = int(row[default_month_col]) if defaulted else horizon_months + 1
        # Stop at default_t (inclusive if defaulted) or horizon
        last_month = min(default_t, horizon_months)
        for t in range(1, last_month + 1):
            entry: dict = {
                'customer_id': cust,
                'month': t,
                'event': int(defaulted and t == default_t),
            }
            for col in feature_cols:
                entry[col] = row[col]
            rows.append(entry)
    return pd.DataFrame(rows)


def fit_discrete_hazard(
    df: pd.DataFrame,
    customer_id_col: str,
    default_col: str,
    default_month_col: str,
    feature_cols: list[str],
    horizon_months: int = 24,
    random_state: int = 42,
) -> HazardModel:
    """Fit a discrete-time hazard model.

    Builds person-period data (one row per (customer, month) at-risk), adds
    a `month` covariate, fits logistic regression predicting the per-period
    event indicator.
    """
    pp = _expand_to_person_periods(
        df,
        customer_id_col,
        default_col,
        default_month_col,
        feature_cols,
        horizon_months,
    )
    feature_cols_full = [*feature_cols, 'month']
    X = pp[feature_cols_full].to_numpy(dtype=float)
    y = pp['event'].to_numpy(dtype=int)
    clf = LogisticRegression(
        max_iter=500,
        random_state=random_state,
        solver='lbfgs',
    )
    clf.fit(X, y)
    return HazardModel(
        classifier=clf,
        feature_names=tuple(feature_cols_full),
        n_time_buckets=horizon_months,
        horizon_months=horizon_months,
    )


def predict_survival(
    model: HazardModel,
    df: pd.DataFrame,
    customer_id_col: str,
    feature_cols: list[str],
) -> list[SurvivalCurve]:
    """Predict per-customer survival curves over the model's horizon.

    For each customer, generates `horizon_months` hazard estimates by
    feeding (features, month=1..H) through the classifier, then derives
    survival = ∏(1 - h_i) and cumulative default = 1 - survival.
    """
    curves: list[SurvivalCurve] = []
    months = list(range(1, model.horizon_months + 1))
    for _, row in df.iterrows():
        base = np.array([row[c] for c in feature_cols], dtype=float)
        hazards: list[float] = []
        for t in months:
            x = np.append(base, t).reshape(1, -1)
            h = float(model.classifier.predict_proba(x)[0, 1])
            hazards.append(h)
        surv: list[float] = []
        cum_def: list[float] = []
        s = 1.0
        for h in hazards:
            s *= 1.0 - h
            surv.append(s)
            cum_def.append(1.0 - s)
        expected_lifetime = sum(surv)
        curves.append(
            SurvivalCurve(
                customer_id=str(row[customer_id_col]),
                months=tuple(months),
                hazard=tuple(hazards),
                survival=tuple(surv),
                cumulative_default=tuple(cum_def),
                expected_lifetime_months=float(expected_lifetime),
            )
        )
    return curves


def survival_to_dataframe(curves: list[SurvivalCurve]) -> pd.DataFrame:
    """Long-format DataFrame for plotting: (customer_id, month, hazard, survival)."""
    rows: list[dict] = []
    for c in curves:
        for i, t in enumerate(c.months):
            rows.append(
                {
                    'customer_id': c.customer_id,
                    'month': t,
                    'hazard': c.hazard[i],
                    'survival': c.survival[i],
                    'cumulative_default': c.cumulative_default[i],
                }
            )
    return pd.DataFrame(rows)


def peak_hazard_month(curve: SurvivalCurve) -> int:
    """Identify the month of highest hazard — used for vintage analysis."""
    idx = int(np.argmax(np.asarray(curve.hazard)))
    return curve.months[idx]
