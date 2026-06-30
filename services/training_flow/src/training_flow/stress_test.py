"""DFAST-style stress testing — Phase D Item A9.

Real banks > $50B in assets are required by the Federal Reserve to run
**DFAST** (Dodd-Frank Act Stress Test) annually: take the current portfolio,
project losses under three macroeconomic scenarios (baseline, adverse,
severely adverse), and report capital adequacy.

This module implements a synthetic-data analog:

1. Define **macro shock vectors** — multiplicative or additive perturbations
   applied to customer features that simulate adverse economic conditions
   (rising unemployment → income drop, falling housing → utilization rise,
   recession → all PD up, etc).
2. **Apply shocks to a cohort snapshot**, producing a stressed cohort.
3. **Re-score the stressed cohort** through the trained model to estimate
   stressed PD per customer.
4. **Aggregate to portfolio EL** under each scenario (using the PD×LGD×EAD
   library from Phase B S4).

Industry-standard scenarios (FRB Y-14A):
- **Baseline**: expected economic conditions
- **Adverse**: moderate recession
- **Severely Adverse**: 2008-style shock with peak unemployment ~10%

We approximate these via DGP-parameter shifts. The result feeds into the
required regulatory capital calculation and any internal "loss budget"
target the lender sets.

Used in `ml_evaluation.ipynb` and registered in MLflow as artifacts tied to
each released model version.

Cloud-agnostic; pure numpy/pandas. Uses the loss_forecasting library (Phase B S4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from training_flow.loss_forecasting import (
    DEFAULT_DRAW_FACTOR,
    DEFAULT_LGD_RETAIL_CARD,
    PortfolioLossResult,
    portfolio_summary,
)


@dataclass(frozen=True, slots=True)
class StressScenario:
    """One DFAST-style scenario definition.

    `feature_shocks` is a dict of column-name → multiplicative or additive
    shock. For multiplicative: column_after = column_before * factor. For
    additive: column_after = column_before + delta. The `apply` method
    handles both.

    `pd_floor` is a per-scenario floor on predicted PD (e.g., severely
    adverse scenarios typically have a 1% PD floor regardless of model
    output, to capture macro-correlation tail risk the model misses).
    """

    name: str
    description: str
    # multiplicative shocks: feature -> multiplier
    multiplicative: dict[str, float] = field(default_factory=dict)
    # additive shocks: feature -> delta
    additive: dict[str, float] = field(default_factory=dict)
    pd_floor: float = 0.0
    lgd_override: float | None = (
        None  # Stress LGD typically higher than base (worse recovery)
    )


# Standard FRB-aligned scenario library
DFAST_BASELINE = StressScenario(
    name='baseline',
    description='Expected economic conditions; no perturbation.',
    multiplicative={},
    additive={},
    pd_floor=0.0,
    lgd_override=None,
)

DFAST_ADVERSE = StressScenario(
    name='adverse',
    description='Moderate recession: unemployment +2pp, income shock, modest util increase.',
    multiplicative={
        'annual_income': 0.90,  # 10% income drop
        'utilization': 1.15,  # 15% util increase
        'avg_utilization_30d': 1.15,
    },
    additive={
        'prev_delinquency_count': 1.0,  # one additional delinquency
    },
    pd_floor=0.02,
    lgd_override=0.85,  # higher LGD under stress
)

DFAST_SEVERELY_ADVERSE = StressScenario(
    name='severely_adverse',
    description='2008-style shock: 20% income drop, sharp util rise, 1% PD floor.',
    multiplicative={
        'annual_income': 0.80,
        'utilization': 1.35,
        'avg_utilization_30d': 1.35,
        'paydown_rate_30d': 0.60,  # paydowns drop sharply
    },
    additive={
        'prev_delinquency_count': 2.0,
    },
    pd_floor=0.05,
    lgd_override=0.90,
)

STANDARD_SCENARIOS: list[StressScenario] = [
    DFAST_BASELINE,
    DFAST_ADVERSE,
    DFAST_SEVERELY_ADVERSE,
]


def apply_scenario(df: pd.DataFrame, scenario: StressScenario) -> pd.DataFrame:
    """Return a copy of `df` with the scenario's shocks applied.

    Missing columns are skipped silently (a scenario may reference features
    that aren't in every model's feature_cols). Non-numeric columns are skipped.
    """
    out = df.copy()
    for col, mult in scenario.multiplicative.items():
        if col in out.columns and pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col] * mult
    for col, delta in scenario.additive.items():
        if col in out.columns and pd.api.types.is_numeric_dtype(out[col]):
            out[col] = out[col] + delta
    return out


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """Aggregated portfolio loss under one scenario."""

    scenario_name: str
    n_customers: int
    portfolio_ead: float
    expected_loss: float
    loss_rate: float
    unexpected_loss: float
    weighted_avg_pd: float
    pd_floor_applied: bool


def run_stress_test(
    df: pd.DataFrame,
    predict_pd_fn: Callable[[pd.DataFrame], np.ndarray],
    current_balance_col: str,
    credit_limit_col: str,
    scenarios: list[StressScenario] | None = None,
    draw_factor: float = DEFAULT_DRAW_FACTOR,
    base_lgd: float = DEFAULT_LGD_RETAIL_CARD,
) -> list[ScenarioResult]:
    """Run a portfolio through each scenario and aggregate losses.

    `predict_pd_fn(df) -> np.ndarray[float]` is the trained model's PD predictor.
    Same function is used for all scenarios — the change is in the input features.

    The output is one ScenarioResult per scenario, suitable for the standard
    "stress test summary table" the CCAR/Y-14A reports require.
    """
    scenario_list = scenarios if scenarios is not None else STANDARD_SCENARIOS
    results: list[ScenarioResult] = []
    for sc in scenario_list:
        stressed = apply_scenario(df, sc)
        pd_values = predict_pd_fn(stressed)
        # Apply PD floor
        pd_values = np.maximum(pd_values, sc.pd_floor)
        pd_floor_applied = bool(sc.pd_floor > 0)

        lgd = sc.lgd_override if sc.lgd_override is not None else base_lgd
        balance = stressed[current_balance_col].to_numpy()
        limit = stressed[credit_limit_col].to_numpy()
        from training_flow.loss_forecasting import compute_ead

        ead = compute_ead(balance, limit, draw_factor=draw_factor)

        portfolio: PortfolioLossResult = portfolio_summary(pd_values, lgd, ead)
        results.append(
            ScenarioResult(
                scenario_name=sc.name,
                n_customers=portfolio.n_accounts,
                portfolio_ead=portfolio.portfolio_ead,
                expected_loss=portfolio.portfolio_expected_loss,
                loss_rate=portfolio.portfolio_loss_rate,
                unexpected_loss=portfolio.portfolio_unexpected_loss,
                weighted_avg_pd=portfolio.weighted_avg_pd,
                pd_floor_applied=pd_floor_applied,
            )
        )
    return results


def stress_test_to_dataframe(results: list[ScenarioResult]) -> pd.DataFrame:
    """Render the standard regulator-style stress-test summary table."""
    rows: list[dict] = []
    for r in results:
        rows.append(
            {
                'scenario': r.scenario_name,
                'n_customers': r.n_customers,
                'portfolio_ead': r.portfolio_ead,
                'expected_loss': r.expected_loss,
                'loss_rate_pct': r.loss_rate * 100,
                'unexpected_loss': r.unexpected_loss,
                'weighted_avg_pd': r.weighted_avg_pd,
                'pd_floor_applied': r.pd_floor_applied,
            }
        )
    return pd.DataFrame(rows)
