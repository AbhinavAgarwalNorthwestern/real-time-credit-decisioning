"""Characteristic Stability Index (CSI) — Phase H Item B12.

PSI (Population Stability Index, already implemented in detectors.py) operates
on the joint distribution of a feature, treating it as a single scalar.
**CSI is the per-feature extension**: compute PSI independently for each input
feature against a reference distribution. Flagging the SPECIFIC feature(s)
that shifted, rather than just saying "something shifted," is what credit
shops actually need for incident response.

CSI is mathematically identical to PSI applied per-feature; the value of this
module is the orchestration + reporting structure that integrates with the
existing drift_monitor service.

Industry standard for credit:
- < 0.10: no significant shift
- 0.10 - 0.25: minor shift, monitor
- > 0.25: significant shift, investigate and possibly retrain

Returns per-feature CSI + classification, ready to wire into drift_monitor's
existing Kafka emission of `drift-events`.

Cloud-agnostic: pure numpy/pandas. No external deps.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class CSIClassification(str, Enum):
    """Industry-standard CSI severity buckets."""

    STABLE = 'stable'  # CSI < 0.10
    MINOR_SHIFT = 'minor'  # 0.10 ≤ CSI < 0.25
    MAJOR_SHIFT = 'major'  # CSI ≥ 0.25


@dataclass(frozen=True, slots=True)
class FeatureCSI:
    """CSI for one feature against a reference distribution."""

    feature: str
    csi: float
    classification: CSIClassification
    n_reference: int
    n_current: int
    n_bins: int


@dataclass(frozen=True, slots=True)
class CSIReport:
    """Aggregate CSI assessment across all monitored features."""

    per_feature: list[FeatureCSI]
    overall_max_csi: float
    n_major_shifts: int
    n_minor_shifts: int


def classify_csi(csi: float) -> CSIClassification:
    """Map a CSI value to the standard severity bucket."""
    if csi < 0.10:
        return CSIClassification.STABLE
    if csi < 0.25:
        return CSIClassification.MINOR_SHIFT
    return CSIClassification.MAJOR_SHIFT


def _psi_from_arrays(
    ref: np.ndarray,
    cur: np.ndarray,
    n_bins: int = 10,
    smooth_eps: float = 1e-6,
) -> float:
    """Population Stability Index between reference and current arrays.

    Bins by reference quantiles for stable cut-points (avoids drift-induced
    bin definitions). Smoothing prevents log(0) when a bin is empty.
    """
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if len(ref) == 0 or len(cur) == 0:
        return 0.0

    # Reference quantile cut-points
    quantiles = np.linspace(0, 1, n_bins + 1)
    cuts = np.unique(np.quantile(ref, quantiles))
    # If too few distinct values, pad cuts so np.digitize works
    if len(cuts) < 2:
        return 0.0
    # Extend to ±inf so all values bin
    cuts[0] = -np.inf
    cuts[-1] = np.inf

    ref_counts, _ = np.histogram(ref, bins=cuts)
    cur_counts, _ = np.histogram(cur, bins=cuts)

    ref_props = ref_counts / max(ref_counts.sum(), 1)
    cur_props = cur_counts / max(cur_counts.sum(), 1)
    ref_props = np.maximum(ref_props, smooth_eps)
    cur_props = np.maximum(cur_props, smooth_eps)

    psi = float(np.sum((cur_props - ref_props) * np.log(cur_props / ref_props)))
    return psi


def compute_csi(
    reference: pd.DataFrame,
    current: pd.DataFrame,
    feature_cols: list[str],
    n_bins: int = 10,
) -> CSIReport:
    """Compute per-feature CSI against a reference window.

    Features not present in BOTH frames are skipped silently. Non-numeric
    columns produce CSI=0 (we can't bin them quantile-wise).
    """
    per_feature: list[FeatureCSI] = []
    for col in feature_cols:
        if col not in reference.columns or col not in current.columns:
            continue
        if not pd.api.types.is_numeric_dtype(reference[col]):
            continue
        ref_arr = reference[col].to_numpy(dtype=float)
        cur_arr = current[col].to_numpy(dtype=float)
        csi = _psi_from_arrays(ref_arr, cur_arr, n_bins=n_bins)
        per_feature.append(
            FeatureCSI(
                feature=col,
                csi=csi,
                classification=classify_csi(csi),
                n_reference=int(np.sum(~np.isnan(ref_arr))),
                n_current=int(np.sum(~np.isnan(cur_arr))),
                n_bins=n_bins,
            )
        )

    csi_values = [f.csi for f in per_feature]
    overall_max = max(csi_values) if csi_values else 0.0
    n_major = sum(
        1 for f in per_feature if f.classification == CSIClassification.MAJOR_SHIFT
    )
    n_minor = sum(
        1 for f in per_feature if f.classification == CSIClassification.MINOR_SHIFT
    )
    return CSIReport(
        per_feature=per_feature,
        overall_max_csi=overall_max,
        n_major_shifts=n_major,
        n_minor_shifts=n_minor,
    )


def csi_report_to_dataframe(report: CSIReport) -> pd.DataFrame:
    """Flatten the report into a tidy DataFrame, sorted by CSI desc."""
    rows = [
        {
            'feature': f.feature,
            'csi': round(f.csi, 4),
            'classification': f.classification.value,
            'n_reference': f.n_reference,
            'n_current': f.n_current,
        }
        for f in report.per_feature
    ]
    return pd.DataFrame(rows).sort_values('csi', ascending=False).reset_index(drop=True)


def csi_violations(report: CSIReport) -> list[str]:
    """Return human-readable strings for drift-event Kafka publishing."""
    out: list[str] = []
    for f in report.per_feature:
        if f.classification == CSIClassification.MAJOR_SHIFT:
            out.append(f'CSI MAJOR: {f.feature} csi={f.csi:.4f} (threshold 0.25)')
        elif f.classification == CSIClassification.MINOR_SHIFT:
            out.append(f'CSI minor: {f.feature} csi={f.csi:.4f} (threshold 0.10)')
    return out
