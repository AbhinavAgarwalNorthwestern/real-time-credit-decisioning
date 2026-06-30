"""Expected Loss decomposition: PD × LGD × EAD — Phase B Item S4.

The Basel-standard credit loss formula decomposes expected loss into three
components that real banks model SEPARATELY:

    EL = PD × LGD × EAD

- **PD** (Probability of Default) — the chance the customer defaults over the
  observation horizon. Output of the main risk model (the T-learner / GBM /
  logistic scorecard). 0 ≤ PD ≤ 1.
- **LGD** (Loss Given Default) — the fraction of exposure NOT recovered
  if default occurs. Typically modeled separately because recoveries depend
  on collateral, product type, jurisdiction. Industry default for unsecured
  credit cards: ~0.75-0.85 (Basel says 75% for retail card).
- **EAD** (Exposure At Default) — the dollar amount at risk if default
  occurs. For credit limit decisions: EAD = min(current_balance + draw_factor *
  (limit - balance), limit). The draw factor captures the empirical fact that
  customers headed for default tend to draw down their available credit.

Beyond expected loss, real shops report:
- **UL** (Unexpected Loss / EL std deviation) — used in regulatory capital
- **EL @ portfolio level** — aggregate over a population
- **Loss-rate curves** at the segment / cohort / product level

The original `services/decisioner/.../inference.py` profit calculation
collapses all three into a single "true_p_default × exposure × LGD" term;
this module splits it explicitly so each component can be calibrated, stress-
tested, and reported on its own.

References:
- Basel III IRB Approach §6 (the regulatory framework)
- Bohn & Stein (2009), "Active Credit Portfolio Management in Practice"
- BCBS 128 (2006), "International Convergence" — original LGD definitions

Used in `ml_evaluation.ipynb` §16 after Step 12.6 retrains the model.
Cloud-agnostic; pure pandas + numpy.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Basel III retail credit card defaults
DEFAULT_LGD_RETAIL_CARD: float = 0.80
DEFAULT_DRAW_FACTOR: float = 0.60  # fraction of available credit drawn pre-default


@dataclass(frozen=True, slots=True)
class LossComponents:
    """Per-account decomposition of expected loss into PD × LGD × EAD."""

    pd_value: float
    lgd: float
    ead: float
    expected_loss: float


@dataclass(frozen=True, slots=True)
class PortfolioLossResult:
    """Aggregate portfolio expected-loss statistics."""

    n_accounts: int
    portfolio_ead: float
    portfolio_expected_loss: float
    portfolio_loss_rate: float
    portfolio_unexpected_loss: float
    weighted_avg_pd: float
    weighted_avg_lgd: float


def compute_ead(
    current_balance: np.ndarray,
    credit_limit: np.ndarray,
    draw_factor: float = DEFAULT_DRAW_FACTOR,
) -> np.ndarray:
    """Exposure At Default per Basel III IRB conversion-factor approach.

    EAD = current_balance + draw_factor × (limit - balance)

    The draw_factor captures the empirical observation that customers approaching
    default tend to draw down available credit before they fail to pay. Basel
    III defaults to 100% for credit cards (full draw assumed); empirical studies
    (Moody's, S&P) suggest 60-75% is more realistic for sub-prime, 40-60% for
    prime. We default to 0.60 — adjust per portfolio.
    """
    available = np.maximum(credit_limit - current_balance, 0.0)
    return current_balance + draw_factor * available


def expected_loss_per_account(
    pd_value: np.ndarray,
    lgd: np.ndarray | float,
    ead: np.ndarray,
) -> np.ndarray:
    """Per-account EL = PD × LGD × EAD.

    `lgd` can be a scalar (e.g., 0.80 portfolio-wide) or per-account (when an
    LGD model is in play). PD must be in [0, 1].
    """
    lgd_arr = np.full_like(pd_value, lgd) if np.isscalar(lgd) else np.asarray(lgd)
    return np.asarray(pd_value) * lgd_arr * np.asarray(ead)


def unexpected_loss_per_account(
    pd_value: np.ndarray,
    lgd: np.ndarray | float,
    ead: np.ndarray,
) -> np.ndarray:
    """Per-account Unexpected Loss = EAD × LGD × sqrt(PD × (1 - PD)).

    The standard deviation of loss under the Bernoulli default model with
    constant LGD per account. Used in regulatory capital (RWA) computation.
    """
    pd_arr = np.asarray(pd_value)
    lgd_arr = np.full_like(pd_arr, lgd) if np.isscalar(lgd) else np.asarray(lgd)
    return np.asarray(ead) * lgd_arr * np.sqrt(pd_arr * (1.0 - pd_arr))


def portfolio_summary(
    pd_values: np.ndarray,
    lgd_values: np.ndarray | float,
    ead_values: np.ndarray,
) -> PortfolioLossResult:
    """Aggregate per-account EL/UL into portfolio-level statistics."""
    n = len(pd_values)
    el = expected_loss_per_account(pd_values, lgd_values, ead_values)
    ul = unexpected_loss_per_account(pd_values, lgd_values, ead_values)
    ead_total = float(np.sum(ead_values))
    el_total = float(np.sum(el))
    ul_total = float(np.sqrt(np.sum(ul**2)))  # assume independence; conservative
    pd_arr = np.asarray(pd_values)
    lgd_arr = (
        np.full_like(pd_arr, lgd_values)
        if np.isscalar(lgd_values)
        else np.asarray(lgd_values)
    )

    return PortfolioLossResult(
        n_accounts=n,
        portfolio_ead=ead_total,
        portfolio_expected_loss=el_total,
        portfolio_loss_rate=el_total / max(ead_total, 1.0),
        portfolio_unexpected_loss=ul_total,
        weighted_avg_pd=float(np.average(pd_arr, weights=ead_values)),
        weighted_avg_lgd=float(np.average(lgd_arr, weights=ead_values)),
    )


def loss_components_frame(
    df: pd.DataFrame,
    pd_col: str,
    current_balance_col: str,
    credit_limit_col: str,
    lgd: float = DEFAULT_LGD_RETAIL_CARD,
    draw_factor: float = DEFAULT_DRAW_FACTOR,
) -> pd.DataFrame:
    """Annotate a per-account DataFrame with EAD, EL, UL columns.

    Returns the input DataFrame with three additional columns. Non-destructive;
    upstream code keeps its prior columns intact.
    """
    required = {pd_col, current_balance_col, credit_limit_col}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f'missing columns: {missing}')

    out = df.copy()
    pd_values = out[pd_col].to_numpy()
    ead = compute_ead(
        out[current_balance_col].to_numpy(),
        out[credit_limit_col].to_numpy(),
        draw_factor=draw_factor,
    )
    out['ead'] = ead
    out['expected_loss'] = expected_loss_per_account(pd_values, lgd, ead)
    out['unexpected_loss'] = unexpected_loss_per_account(pd_values, lgd, ead)
    return out


def loss_rate_by_segment(
    df: pd.DataFrame,
    pd_col: str,
    current_balance_col: str,
    credit_limit_col: str,
    segment_col: str,
    lgd: float = DEFAULT_LGD_RETAIL_CARD,
    draw_factor: float = DEFAULT_DRAW_FACTOR,
) -> pd.DataFrame:
    """Loss rate (EL / EAD) per segment — the headline portfolio dashboard chart.

    Returns a DataFrame with: segment, n_accounts, portfolio_ead, expected_loss,
    loss_rate, weighted_avg_pd.
    """
    annotated = loss_components_frame(
        df,
        pd_col,
        current_balance_col,
        credit_limit_col,
        lgd=lgd,
        draw_factor=draw_factor,
    )
    grouped = (
        annotated.groupby(segment_col)
        .agg(
            n_accounts=(pd_col, 'size'),
            portfolio_ead=('ead', 'sum'),
            expected_loss=('expected_loss', 'sum'),
            weighted_avg_pd=(pd_col, 'mean'),
        )
        .reset_index()
    )
    grouped['loss_rate'] = grouped['expected_loss'] / grouped['portfolio_ead'].clip(
        lower=1.0
    )
    return grouped
