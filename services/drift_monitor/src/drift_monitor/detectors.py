"""Day-5: Drift detection algorithms.

Three complementary detectors:
  - PSI (Population Stability Index) — feature distribution drift
  - KS (Kolmogorov-Smirnov) — continuous-distribution shift
  - ADWIN (ADaptive WINdowing) — concept drift in the decision stream
    over time, with adaptive window resizing

Each returns a `DriftSignal` describing the magnitude, the affected
feature, and whether it crosses the configured alert threshold.
Extensible by implementing the `Detector` protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
from scipy import stats


@dataclass(frozen=True, slots=True)
class DriftSignal:
    detector: str
    feature: str
    statistic: float
    threshold: float
    is_drifting: bool


class Detector(Protocol):
    name: str

    def detect(self, ref: np.ndarray, cur: np.ndarray, feature: str) -> DriftSignal: ...


@dataclass(frozen=True, slots=True)
class PSIDetector:
    n_bins: int = 10
    threshold: float = 0.2
    name: str = 'psi'

    def detect(self, ref: np.ndarray, cur: np.ndarray, feature: str) -> DriftSignal:
        # Bin edges from the reference distribution
        ref = ref[~np.isnan(ref)]
        cur = cur[~np.isnan(cur)]
        if len(ref) == 0 or len(cur) == 0:
            return DriftSignal(self.name, feature, 0.0, self.threshold, False)
        quantiles = np.linspace(0, 1, self.n_bins + 1)
        edges = np.unique(np.quantile(ref, quantiles))
        if len(edges) < 2:
            return DriftSignal(self.name, feature, 0.0, self.threshold, False)
        ref_hist, _ = np.histogram(ref, bins=edges)
        cur_hist, _ = np.histogram(cur, bins=edges)
        eps = 1e-6
        ref_pct = ref_hist / max(ref_hist.sum(), 1) + eps
        cur_pct = cur_hist / max(cur_hist.sum(), 1) + eps
        psi = float(((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)).sum())
        return DriftSignal(
            self.name, feature, psi, self.threshold, psi > self.threshold
        )


@dataclass(frozen=True, slots=True)
class KSDetector:
    threshold: float = 0.05  # p-value threshold (lower = significant drift)
    name: str = 'ks'

    def detect(self, ref: np.ndarray, cur: np.ndarray, feature: str) -> DriftSignal:
        ref = ref[~np.isnan(ref)]
        cur = cur[~np.isnan(cur)]
        if len(ref) < 30 or len(cur) < 30:
            return DriftSignal(self.name, feature, 1.0, self.threshold, False)
        stat, p = stats.ks_2samp(ref, cur)
        return DriftSignal(
            self.name, feature, float(p), self.threshold, p < self.threshold
        )


class ADWINDetector:
    """Simple ADWIN-style: maintain a window, detect a split point where
    the means of the two sub-windows differ significantly per Hoeffding."""

    name = 'adwin'

    def __init__(self, delta: float = 0.002) -> None:
        self.delta = delta
        self._window: list[float] = []

    def push(self, value: float) -> bool:
        """Append; return True if a drift split was detected (and resets
        the window to the post-split half)."""
        self._window.append(value)
        if len(self._window) < 30:
            return False
        for i in range(10, len(self._window) - 10):
            left = np.array(self._window[:i])
            right = np.array(self._window[i:])
            m_left, m_right = left.mean(), right.mean()
            n_left, n_right = len(left), len(right)
            # Hoeffding-style bound on mean difference
            m = 1 / (1 / n_left + 1 / n_right)
            bound = float(np.sqrt((1 / (2 * m)) * np.log(2 / self.delta)))
            if abs(m_left - m_right) > bound:
                self._window = self._window[i:]
                return True
        return False


@dataclass(frozen=True, slots=True)
class JSDivergenceDetector:
    """Prediction drift: Jensen-Shannon divergence between two distributions.

    Use case: detect that the *model's output* distribution has shifted
    even if feature distributions haven't. Often an earlier signal than
    realized performance regression.
    """

    n_bins: int = 20
    threshold: float = 0.1  # JS divergence > 0.1 ≈ noticeable shift
    name: str = 'js_divergence'

    def detect(self, ref: np.ndarray, cur: np.ndarray, feature: str) -> DriftSignal:
        from scipy.spatial.distance import jensenshannon

        ref = ref[~np.isnan(ref)]
        cur = cur[~np.isnan(cur)]
        if len(ref) < 30 or len(cur) < 30:
            return DriftSignal(self.name, feature, 0.0, self.threshold, False)
        lo = float(min(ref.min(), cur.min()))
        hi = float(max(ref.max(), cur.max()))
        edges = np.linspace(lo, hi, self.n_bins + 1)
        ref_p = np.histogram(ref, bins=edges)[0] + 1e-9
        cur_p = np.histogram(cur, bins=edges)[0] + 1e-9
        ref_p = ref_p / ref_p.sum()
        cur_p = cur_p / cur_p.sum()
        js = float(jensenshannon(ref_p, cur_p))
        return DriftSignal(self.name, feature, js, self.threshold, js > self.threshold)


@dataclass(frozen=True, slots=True)
class PerformanceDriftDetector:
    """Performance drift: OPE-estimated profit vs realized actual profit.

    Compares the Day-6 OPE estimate (what we thought we'd earn) against
    the realized outcomes that come back. If the gap widens, the model's
    calibration to the live world has decayed — fire drift.
    """

    relative_gap_threshold: float = 0.15  # 15% relative gap
    name: str = 'performance_gap'

    def detect_gap(
        self,
        predicted_profit: np.ndarray,
        realized_profit: np.ndarray,
        feature: str = 'overall',
    ) -> DriftSignal:
        if len(predicted_profit) < 50 or len(realized_profit) < 50:
            return DriftSignal(
                self.name, feature, 0.0, self.relative_gap_threshold, False
            )
        mean_pred = float(np.mean(predicted_profit))
        mean_real = float(np.mean(realized_profit))
        if abs(mean_pred) < 1e-6:
            gap = 0.0
        else:
            gap = abs(mean_pred - mean_real) / abs(mean_pred)
        return DriftSignal(
            self.name,
            feature,
            gap,
            self.relative_gap_threshold,
            gap > self.relative_gap_threshold,
        )


@dataclass(frozen=True, slots=True)
class SchemaDriftDetector:
    """Data-quality drift: missingness rate, dtype changes, range violations.

    Cheap but high-value: a schema regression usually precedes a model
    failure by hours, and is the easiest type of drift to root-cause.
    """

    missingness_max_increase: float = 0.05  # 5pp absolute
    name: str = 'schema'

    def detect_missingness(
        self, ref_missing_pct: float, cur_missing_pct: float, feature: str
    ) -> DriftSignal:
        delta = cur_missing_pct - ref_missing_pct
        return DriftSignal(
            self.name,
            feature,
            delta,
            self.missingness_max_increase,
            delta > self.missingness_max_increase,
        )


class PerSegmentDriftMonitor:
    """Stratified PSI / KS per segment. Catches drift that aggregates
    away at the cohort level (one segment changes; cohort-mean is
    unchanged because the others compensate)."""

    name = 'per_segment'

    def __init__(self, base_detector: Detector) -> None:
        self.base_detector = base_detector

    def detect_per_segment(
        self,
        ref_df: 'pd.DataFrame',  # type: ignore[name-defined] # noqa: F821
        cur_df: 'pd.DataFrame',  # type: ignore[name-defined] # noqa: F821
        feature: str,
        segment_col: str = 'segment_id',
    ) -> list[DriftSignal]:
        out: list[DriftSignal] = []
        for seg in sorted(ref_df[segment_col].dropna().unique()):
            ref_v = ref_df[ref_df[segment_col] == seg][feature].to_numpy()
            cur_v = cur_df[cur_df[segment_col] == seg][feature].to_numpy()
            sig = self.base_detector.detect(
                ref_v, cur_v, f'{feature}@segment_{int(seg)}'
            )
            out.append(sig)
        return out
