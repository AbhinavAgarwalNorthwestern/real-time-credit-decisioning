"""Weight-of-Evidence (WoE) binning + Information Value (IV) — Phase B S6.

Pre-model credit scorecard utilities. Used in `notebooks/01_customer_eda.ipynb`
§8 to rank the 5 customer attributes by predictive power before any model is
trained, and to produce monotonically-binned encodings that a traditional
credit scorecard (logistic regression on WoE features) would consume.

Industry references:
- Siddiqi, "Credit Risk Scorecards" (2nd ed.), Wiley 2017 — IV strength conventions
- Mays, "Handbook of Credit Scoring" (2001) — WoE binning + monotonicity

Conventions:
- IV strength: < 0.02 unpredictive | 0.02-0.10 weak | 0.10-0.30 medium |
               0.30-0.50 strong | > 0.50 suspicious (target leakage warning)
- Laplace smoothing: add 0.5 to event/non-event counts to avoid log(0) when a
  bin has zero of one class.

Pure pandas + numpy; no cluster, no cloud, no network. Same code runs
identically on any cloud — see `feedback_cloud_agnostic` memory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

_LAPLACE_EPS = 0.5


@dataclass(frozen=True, slots=True)
class WoeBin:
    """One bin in a WoE encoding: range [lo, hi], counts, WoE, IV contribution."""

    lo: float
    hi: float
    n_total: int
    n_events: int
    n_non_events: int
    event_rate: float
    woe: float
    iv_contribution: float


@dataclass(frozen=True, slots=True)
class WoeBinningResult:
    """Output of `compute_woe` for one feature.

    `bins` are sorted by ascending `lo`. `monotonic_in_event_rate` is True when
    event_rate is strictly monotonic across the bins (precondition for safe
    monotonic-constrained downstream modeling per Phase B S5).
    """

    feature: str
    bins: list[WoeBin] = field(default_factory=list)
    iv_total: float = 0.0
    monotonic_in_event_rate: bool = False
    n_unique_values: int = 0
    target_event_rate: float = 0.0


def iv_strength_label(iv: float) -> str:
    """Siddiqi (2017) IV strength buckets."""
    if iv < 0.02:
        return 'unpredictive'
    if iv < 0.10:
        return 'weak'
    if iv < 0.30:
        return 'medium'
    if iv < 0.50:
        return 'strong'
    return 'suspicious'


def _smoothed_dist(count: int, total: int, eps: float = _LAPLACE_EPS) -> float:
    """Laplace-smoothed distribution share: when count is 0, use eps instead.

    Smoothing is applied at the COUNT level (not the dist level) so that a
    truly tiny share like 0.001 isn't clobbered by eps. Only zero counts get
    bumped to eps, which is the standard credit-scorecard convention
    (Siddiqi 2017 §6.3).
    """
    numer = float(count) if count > 0 else eps
    denom = float(total) if total > 0 else eps
    return numer / denom


def _build_bin(
    lo: float,
    hi: float,
    feature_slice: pd.Series,
    target_slice: pd.Series,
    total_events: int,
    total_non_events: int,
) -> WoeBin:
    n_total = len(feature_slice)
    n_events = int(target_slice.sum())
    n_non_events = n_total - n_events
    event_rate = n_events / n_total if n_total > 0 else 0.0

    dist_event = _smoothed_dist(n_events, total_events)
    dist_non_event = _smoothed_dist(n_non_events, total_non_events)
    woe = float(np.log(dist_non_event / dist_event))
    iv_contribution = (dist_non_event - dist_event) * woe

    return WoeBin(
        lo=float(lo),
        hi=float(hi),
        n_total=n_total,
        n_events=n_events,
        n_non_events=n_non_events,
        event_rate=event_rate,
        woe=woe,
        iv_contribution=iv_contribution,
    )


def compute_woe(
    feature: pd.Series,
    target: pd.Series,
    n_bins: int = 10,
    name: str | None = None,
) -> WoeBinningResult:
    """Compute WoE binning + IV for one feature against a binary target.

    Continuous features: equal-frequency (quantile) binning with `n_bins`.
    Discrete features (≤ n_bins unique values): each unique value is its own bin.

    Parameters
    ----------
    feature
        Continuous or discrete numeric input column.
    target
        Binary {0, 1} outcome column aligned with `feature`.
    n_bins
        Max number of bins for quantile binning. Discrete features may produce
        fewer bins.
    name
        Optional feature name (for the result object).
    """
    if len(feature) != len(target):
        raise ValueError(
            f'feature ({len(feature)}) and target ({len(target)}) length mismatch'
        )

    df = pd.DataFrame({'x': feature.to_numpy(), 'y': target.to_numpy()}).dropna()
    n_total_events = int(df['y'].sum())
    n_total_non_events = len(df) - n_total_events
    target_rate = n_total_events / max(len(df), 1)
    n_unique = int(df['x'].nunique())

    feature_name = name or (feature.name if feature.name is not None else 'feature')

    bins: list[WoeBin] = []

    if n_unique <= n_bins:
        for value in sorted(df['x'].unique()):
            sub = df[df['x'] == value]
            bins.append(
                _build_bin(
                    lo=float(value),
                    hi=float(value),
                    feature_slice=sub['x'],
                    target_slice=sub['y'],
                    total_events=n_total_events,
                    total_non_events=n_total_non_events,
                )
            )
    else:
        df_sorted = df.sort_values('x').reset_index(drop=True)
        try:
            df_sorted['bin'] = pd.qcut(
                df_sorted['x'], q=n_bins, duplicates='drop', labels=False
            )
        except ValueError:
            df_sorted['bin'] = 0
        for bin_id, group in df_sorted.groupby('bin'):
            lo = float(group['x'].min())
            hi = float(group['x'].max())
            bins.append(
                _build_bin(
                    lo=lo,
                    hi=hi,
                    feature_slice=group['x'],
                    target_slice=group['y'],
                    total_events=n_total_events,
                    total_non_events=n_total_non_events,
                )
            )

    bins = sorted(bins, key=lambda b: b.lo)
    iv_total = sum(b.iv_contribution for b in bins)
    event_rates = [b.event_rate for b in bins]
    monotonic = bool(
        len(event_rates) <= 1
        or all(
            event_rates[i] <= event_rates[i + 1] for i in range(len(event_rates) - 1)
        )
        or all(
            event_rates[i] >= event_rates[i + 1] for i in range(len(event_rates) - 1)
        )
    )

    return WoeBinningResult(
        feature=str(feature_name),
        bins=bins,
        iv_total=float(iv_total),
        monotonic_in_event_rate=monotonic,
        n_unique_values=n_unique,
        target_event_rate=float(target_rate),
    )


def apply_woe(values: pd.Series, result: WoeBinningResult) -> pd.Series:
    """Encode a column using a fitted WoE binning. Returns a Series of WoE floats.

    For values outside the fitted range, uses the WoE of the nearest bin
    (Siddiqi's "edge case" convention — never returns NaN for in-range data).
    """
    if not result.bins:
        return pd.Series(np.zeros(len(values), dtype=float), index=values.index)

    out = np.full(len(values), np.nan, dtype=float)
    arr = values.to_numpy()
    for i, v in enumerate(arr):
        if pd.isna(v):
            continue
        # Find the bin that contains v (or nearest if out of range)
        for b in result.bins:
            if b.lo <= v <= b.hi:
                out[i] = b.woe
                break
        if np.isnan(out[i]):
            # Out of range — use nearest-bin WoE
            if v < result.bins[0].lo:
                out[i] = result.bins[0].woe
            else:
                out[i] = result.bins[-1].woe

    return pd.Series(out, index=values.index)


def rank_features_by_iv(
    df: pd.DataFrame,
    target: pd.Series,
    feature_cols: list[str],
    n_bins: int = 10,
) -> pd.DataFrame:
    """Rank candidate features by Information Value against a binary target.

    Returns a DataFrame with columns:
        feature, iv, strength, monotonic, n_bins
    sorted descending by IV.
    """
    rows: list[dict[str, object]] = []
    for col in feature_cols:
        result = compute_woe(df[col], target, n_bins=n_bins, name=col)
        rows.append(
            {
                'feature': col,
                'iv': round(result.iv_total, 4),
                'strength': iv_strength_label(result.iv_total),
                'monotonic': result.monotonic_in_event_rate,
                'n_bins': len(result.bins),
            }
        )
    out = pd.DataFrame(rows).sort_values('iv', ascending=False).reset_index(drop=True)
    return out


def simulate_binary_target(
    true_p: pd.Series,
    seed: int = 42,
) -> pd.Series:
    """Sample binary default events from continuous true_p_default values.

    The DGP exposes `true_p_default` as a latent probability. Real credit data
    delivers binary default outcomes. WoE / IV require a binary target, so
    this helper draws one Bernoulli realization per row. Deterministic given
    `seed`.
    """
    rng = np.random.default_rng(seed)
    p = true_p.clip(lower=0.0, upper=1.0).to_numpy()
    return pd.Series(rng.binomial(1, p), index=true_p.index, dtype=int)
