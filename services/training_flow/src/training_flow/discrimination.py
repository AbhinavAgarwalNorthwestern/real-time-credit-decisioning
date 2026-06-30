"""Discrimination metrics — Phase B Item S3.

Standard credit-industry rank-ordering metrics for binary PD models. While
sklearn provides AUC and accuracy out of the box, credit shops report
**Gini**, **KS**, and **Lorenz** numbers in every scorecard writeup because
they translate to direct business metrics:

- **Gini** = 2·AUC − 1 ∈ [0, 1]. Perfect rank-ordering = 1.0; random = 0.0.
  Industry expectation for an application-PD model: Gini > 0.40; for a
  behavior-scoring (in-life) model: > 0.55. Below 0.30 the model is
  considered insufficient for regulator-grade decisioning.
- **KS statistic** = max separation between the CDFs of event vs non-event
  predictions across all thresholds. Tells you the optimal cutoff and the
  expected separation at that cutoff. Industry rule of thumb: KS > 0.30 is
  deployable; > 0.50 is excellent.
- **Lorenz curve** = cumulative % of bads captured vs cumulative % of
  population, sorted by descending predicted PD. Used to size cutoff
  strategies ("what % of bads do we catch if we deny the top X% riskiest?").

Used in `ml_evaluation.ipynb` §15 after Step 12.6 retrains the 26-feature model.

References:
- Thomas, Edelman, Crook (2002), "Credit Scoring and its Applications" §7
- Siddiqi (2017), "Credit Risk Scorecards" §7.5

Cloud-agnostic: pure pandas + numpy + sklearn.metrics. No cluster.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score


@dataclass(frozen=True, slots=True)
class LorenzPoint:
    """One point on the Lorenz curve.

    `cum_pop_pct` is the cumulative fraction of population (sorted by
    descending predicted PD); `cum_event_pct` is the cumulative fraction
    of actual events captured at or before that cutoff.
    """

    decile: int
    cum_pop_pct: float
    cum_event_pct: float
    cutoff_pred: float
    event_rate_in_decile: float


@dataclass(frozen=True, slots=True)
class DiscriminationResult:
    """All standard rank-ordering metrics for one model on one slice of data."""

    n_observations: int
    n_events: int
    auc: float
    gini: float
    ks_statistic: float
    ks_threshold: float
    lorenz_curve: list[LorenzPoint]
    # Capture rate at the riskiest decile — operational metric for cutoff strategies
    top_decile_capture_rate: float


def gini_coefficient(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Gini = 2 * AUC - 1. Perfect = 1.0; random = 0.0; perverse = -1.0."""
    if len(y_true) != len(y_pred):
        raise ValueError(f'length mismatch: {len(y_true)} vs {len(y_pred)}')
    if len(np.unique(y_true)) < 2:
        raise ValueError('cannot compute Gini when only one class is present')
    auc = float(roc_auc_score(y_true, y_pred))
    return 2.0 * auc - 1.0


def ks_statistic(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[float, float]:
    """Kolmogorov-Smirnov separation between event and non-event CDFs.

    Returns (ks_stat, threshold_at_max_separation). KS is the largest
    vertical gap between the cumulative distribution functions of predicted
    probabilities for events vs non-events.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f'length mismatch: {len(y_true)} vs {len(y_pred)}')

    df = (
        pd.DataFrame({'p': y_pred, 'y': y_true}).sort_values('p').reset_index(drop=True)
    )
    n_events = int(df['y'].sum())
    n_non_events = len(df) - n_events
    if n_events == 0 or n_non_events == 0:
        raise ValueError('need both event and non-event observations to compute KS')

    df['cum_event'] = df['y'].cumsum() / n_events
    df['cum_non_event'] = (1 - df['y']).cumsum() / n_non_events
    df['gap'] = (df['cum_non_event'] - df['cum_event']).abs()

    idx_max = int(df['gap'].idxmax())
    return float(df['gap'].iloc[idx_max]), float(df['p'].iloc[idx_max])


def lorenz_curve(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> list[LorenzPoint]:
    """Lorenz / cumulative gains curve.

    Sorts by descending predicted PD; the first decile is the riskiest. For
    each decile, reports cumulative population percent vs cumulative event
    percent. Perfect ranking: top X% of population captures > X% of events.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f'length mismatch: {len(y_true)} vs {len(y_pred)}')
    if len(y_true) < n_bins:
        raise ValueError(
            f'need >= {n_bins} obs for {n_bins} deciles, got {len(y_true)}'
        )

    df = (
        pd.DataFrame({'p': y_pred, 'y': y_true})
        .sort_values('p', ascending=False)
        .reset_index(drop=True)
    )
    n_total = len(df)
    n_events = int(df['y'].sum())
    df['decile'] = pd.qcut(df.index, q=n_bins, duplicates='drop', labels=False)

    points: list[LorenzPoint] = []
    cum_pop = 0
    cum_events = 0
    for decile_idx, group in df.groupby('decile'):
        n_in = len(group)
        events_in = int(group['y'].sum())
        cum_pop += n_in
        cum_events += events_in
        points.append(
            LorenzPoint(
                decile=int(decile_idx),
                cum_pop_pct=cum_pop / n_total,
                cum_event_pct=cum_events / max(n_events, 1),
                cutoff_pred=float(group['p'].min()),
                event_rate_in_decile=events_in / max(n_in, 1),
            )
        )
    return points


def discrimination_summary(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> DiscriminationResult:
    """One-stop rank-ordering evaluation: AUC + Gini + KS + Lorenz."""
    y_true_arr = np.asarray(y_true)
    y_pred_arr = np.asarray(y_pred)
    auc = float(roc_auc_score(y_true_arr, y_pred_arr))
    gini = 2.0 * auc - 1.0
    ks, ks_t = ks_statistic(y_true_arr, y_pred_arr)
    lorenz = lorenz_curve(y_true_arr, y_pred_arr, n_bins=n_bins)
    return DiscriminationResult(
        n_observations=len(y_true_arr),
        n_events=int(y_true_arr.sum()),
        auc=auc,
        gini=gini,
        ks_statistic=ks,
        ks_threshold=ks_t,
        lorenz_curve=lorenz,
        top_decile_capture_rate=lorenz[0].cum_event_pct if lorenz else 0.0,
    )


def gini_interpretation(gini: float) -> str:
    """Industry rule-of-thumb classification of a Gini value."""
    if gini >= 0.55:
        return 'excellent'
    if gini >= 0.40:
        return 'good (deployable application PD)'
    if gini >= 0.30:
        return 'marginal'
    return 'insufficient for regulator-grade decisioning'


def ks_interpretation(ks: float) -> str:
    """Industry rule-of-thumb classification of a KS value."""
    if ks >= 0.50:
        return 'excellent'
    if ks >= 0.30:
        return 'deployable'
    return 'insufficient'
