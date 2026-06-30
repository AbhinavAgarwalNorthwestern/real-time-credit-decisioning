"""Calibration testing + isotonic recalibration — Phase B Item S1.

Post-model utilities for credit-decisioning model evaluation. A PD model can
rank-order customers perfectly (high Gini, see `discrimination.py`) yet still
be badly calibrated — and pricing, expected loss, and capital calculations all
depend on calibrated probabilities, not just rankings. This module provides:

- **Hosmer-Lemeshow goodness-of-fit test** — the regulator-standard calibration
  hypothesis test (Hosmer & Lemeshow 2000); χ²-distributed under H₀ (well calibrated)
- **Brier score** — mean squared error between predicted prob and actual outcome,
  decomposable into reliability + resolution + uncertainty (Murphy 1973)
- **Calibration curves** — predicted vs actual per decile of predicted probability;
  the y=x diagonal is perfect calibration
- **Isotonic recalibration** — fits a monotonic post-hoc map from raw predicted
  probabilities to calibrated probabilities (Niculescu-Mizil & Caruana 2005);
  the standard "wrap your model in a recalibrator" pattern at JPMC/CapitalOne

Used in `ml_evaluation.ipynb` §13 after Step 12.6 retrains the 26-feature model.

Cloud-agnostic: pure pandas + numpy + scipy.stats + sklearn.isotonic. No cluster.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One bin of a calibration curve."""

    bin_index: int
    n: int
    mean_predicted: float
    mean_actual: float
    n_events: int
    lower_edge: float
    upper_edge: float


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """Full calibration evaluation for one model on one slice of data."""

    n_observations: int
    n_bins: int
    brier_score: float
    hl_statistic: float
    hl_p_value: float
    hl_df: int
    bins: list[CalibrationBin]
    # Mean absolute calibration error across bins, weighted by bin size.
    weighted_calibration_error: float


def brier_score(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean squared error between predicted probabilities and binary outcomes.

    Lower is better. A model that predicts the base rate everywhere achieves
    Brier = p(1-p); a perfect model achieves 0.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f'length mismatch: {len(y_true)} vs {len(y_pred)}')
    if len(y_true) == 0:
        return 0.0
    diff = y_pred.astype(float) - y_true.astype(float)
    return float(np.mean(diff * diff))


def hosmer_lemeshow_test(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> tuple[float, float, int]:
    """Hosmer-Lemeshow goodness-of-fit test.

    Bins observations by predicted probability into `n_bins` equal-frequency
    buckets, then compares observed vs expected event counts per bin. Returns
    (HL_statistic, p_value, df). Under H₀ (well calibrated), HL ~ χ²(n_bins - 2).

    A small p-value (< 0.05) means the model is NOT well calibrated.

    References: Hosmer & Lemeshow (2000), "Applied Logistic Regression" ch. 5.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f'length mismatch: {len(y_true)} vs {len(y_pred)}')
    if len(y_true) < n_bins:
        raise ValueError(
            f'need >= {n_bins} obs for {n_bins}-bin HL test, got {len(y_true)}'
        )

    df = (
        pd.DataFrame({'p': y_pred, 'y': y_true}).sort_values('p').reset_index(drop=True)
    )
    df['bin'] = pd.qcut(df['p'], q=n_bins, duplicates='drop', labels=False)

    hl = 0.0
    for _bin_idx, group in df.groupby('bin'):
        n_i = len(group)
        observed = int(group['y'].sum())
        expected = float(group['p'].sum())
        # Clip expected so we never divide by zero
        e_pos = max(expected, 1e-9)
        e_neg = max(n_i - expected, 1e-9)
        hl += (observed - expected) ** 2 / e_pos + (
            (n_i - observed) - (n_i - expected)
        ) ** 2 / e_neg

    df_chi2 = max(int(df['bin'].nunique() - 2), 1)
    p_value = float(1.0 - stats.chi2.cdf(hl, df=df_chi2))
    return float(hl), p_value, df_chi2


def calibration_curve(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> list[CalibrationBin]:
    """Return per-bin calibration data: mean predicted vs mean actual."""
    df = (
        pd.DataFrame({'p': y_pred, 'y': y_true}).sort_values('p').reset_index(drop=True)
    )
    df['bin'] = pd.qcut(df['p'], q=n_bins, duplicates='drop', labels=False)

    bins: list[CalibrationBin] = []
    for bin_idx, group in df.groupby('bin'):
        n = len(group)
        bins.append(
            CalibrationBin(
                bin_index=int(bin_idx),
                n=n,
                mean_predicted=float(group['p'].mean()),
                mean_actual=float(group['y'].mean()),
                n_events=int(group['y'].sum()),
                lower_edge=float(group['p'].min()),
                upper_edge=float(group['p'].max()),
            )
        )
    return sorted(bins, key=lambda b: b.lower_edge)


def calibration_summary(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    n_bins: int = 10,
) -> CalibrationResult:
    """One-stop calibration evaluation: HL test + Brier + curve + ECE."""
    y_true_arr = np.asarray(y_true)
    y_pred_arr = np.asarray(y_pred)

    bins = calibration_curve(y_true_arr, y_pred_arr, n_bins=n_bins)
    bs = brier_score(y_true_arr, y_pred_arr)
    hl_stat, hl_p, hl_df = hosmer_lemeshow_test(y_true_arr, y_pred_arr, n_bins=n_bins)

    # Expected Calibration Error (ECE): weighted mean abs(mean_pred - mean_actual)
    total_n = sum(b.n for b in bins)
    weighted_err = sum(b.n * abs(b.mean_predicted - b.mean_actual) for b in bins) / max(
        total_n, 1
    )

    return CalibrationResult(
        n_observations=len(y_true_arr),
        n_bins=len(bins),
        brier_score=bs,
        hl_statistic=hl_stat,
        hl_p_value=hl_p,
        hl_df=hl_df,
        bins=bins,
        weighted_calibration_error=float(weighted_err),
    )


def fit_isotonic_recalibrator(
    y_true: np.ndarray,
    y_pred: np.ndarray,
) -> IsotonicRegression:
    """Fit a monotonic post-hoc recalibrator (Niculescu-Mizil & Caruana 2005).

    The returned model wraps any base PD model: at serving time you call
    `isotonic.predict(raw_pred)` to get a calibrated probability. The mapping
    is non-parametric, monotonic, and trained to minimize Brier score.

    Best practice in production: fit on a HELD-OUT calibration set (NOT the
    training set), to avoid optimistically-biased calibration.
    """
    model = IsotonicRegression(out_of_bounds='clip', y_min=0.0, y_max=1.0)
    model.fit(y_pred, y_true)
    return model


def apply_isotonic(
    y_pred: np.ndarray,
    model: IsotonicRegression,
) -> np.ndarray:
    """Apply a fitted isotonic recalibrator. Returns calibrated probabilities."""
    return np.asarray(model.predict(y_pred), dtype=float)


def segment_calibration(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    segments: np.ndarray,
    n_bins: int = 10,
) -> dict[int, CalibrationResult]:
    """Compute calibration per segment.

    Useful for spotting models that are well-calibrated globally but biased
    in specific subgroups (a common failure mode + fair-lending red flag).
    """
    seg_arr = np.asarray(segments)
    results: dict[int, CalibrationResult] = {}
    for seg_id in np.unique(seg_arr):
        mask = seg_arr == seg_id
        if int(mask.sum()) < n_bins:
            continue  # too few observations for a meaningful HL test
        results[int(seg_id)] = calibration_summary(
            y_true[mask], y_pred[mask], n_bins=n_bins
        )
    return results
