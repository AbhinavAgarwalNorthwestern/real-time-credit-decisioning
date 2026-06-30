"""Unit tests for per-variable CSI (Phase H B12)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from drift_monitor.csi import (
    CSIClassification,
    classify_csi,
    compute_csi,
    csi_report_to_dataframe,
    csi_violations,
)


def test_classify_csi_buckets() -> None:
    assert classify_csi(0.00) == CSIClassification.STABLE
    assert classify_csi(0.05) == CSIClassification.STABLE
    assert classify_csi(0.10) == CSIClassification.MINOR_SHIFT
    assert classify_csi(0.20) == CSIClassification.MINOR_SHIFT
    assert classify_csi(0.25) == CSIClassification.MAJOR_SHIFT
    assert classify_csi(1.50) == CSIClassification.MAJOR_SHIFT


def test_identical_distributions_yield_small_csi() -> None:
    """Same distribution → CSI close to 0."""
    rng = np.random.default_rng(seed=42)
    df = pd.DataFrame({'x': rng.normal(0, 1, size=5000)})
    report = compute_csi(df, df, ['x'])
    assert len(report.per_feature) == 1
    assert report.per_feature[0].csi < 0.01
    assert report.per_feature[0].classification == CSIClassification.STABLE


def test_shifted_distributions_yield_large_csi() -> None:
    """Mean-shifted distribution → CSI > 0.25 (major shift)."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame({'x': rng.normal(0, 1, size=5000)})
    cur = pd.DataFrame({'x': rng.normal(2.0, 1, size=5000)})  # shifted by 2 SDs
    report = compute_csi(ref, cur, ['x'])
    assert report.per_feature[0].csi > 0.25
    assert report.per_feature[0].classification == CSIClassification.MAJOR_SHIFT


def test_minor_shift_in_intermediate_range() -> None:
    """Small mean shift → minor shift bucket."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame({'x': rng.normal(0, 1, size=5000)})
    cur = pd.DataFrame({'x': rng.normal(0.3, 1, size=5000)})
    report = compute_csi(ref, cur, ['x'])
    csi = report.per_feature[0].csi
    assert 0.05 < csi < 0.30


def test_multiple_features_independent_csi() -> None:
    """CSI is computed per feature; one stable + one shifted → mixed report."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame(
        {
            'stable_feat': rng.normal(0, 1, size=3000),
            'shifted_feat': rng.normal(0, 1, size=3000),
        }
    )
    cur = pd.DataFrame(
        {
            'stable_feat': rng.normal(0, 1, size=3000),
            'shifted_feat': rng.normal(2.5, 1, size=3000),
        }
    )
    report = compute_csi(ref, cur, ['stable_feat', 'shifted_feat'])
    by_name = {f.feature: f for f in report.per_feature}
    assert by_name['stable_feat'].classification == CSIClassification.STABLE
    assert by_name['shifted_feat'].classification == CSIClassification.MAJOR_SHIFT


def test_missing_feature_skipped_silently() -> None:
    """Features absent from either frame are skipped, not errored."""
    ref = pd.DataFrame({'x': [1.0, 2, 3]})
    cur = pd.DataFrame({'y': [1.0, 2, 3]})
    report = compute_csi(ref, cur, ['x', 'y', 'z'])
    assert report.per_feature == []


def test_non_numeric_feature_skipped() -> None:
    """Categorical / string features can't be quantile-binned; skipped."""
    ref = pd.DataFrame({'category': ['a', 'b', 'c'] * 100})
    cur = pd.DataFrame({'category': ['a', 'b', 'c'] * 100})
    report = compute_csi(ref, cur, ['category'])
    assert report.per_feature == []


def test_report_to_dataframe_sorts_descending() -> None:
    """Output DataFrame is sorted by CSI desc for incident triage."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame(
        {
            'a': rng.normal(0, 1, size=2000),
            'b': rng.normal(0, 1, size=2000),
            'c': rng.normal(0, 1, size=2000),
        }
    )
    cur = pd.DataFrame(
        {
            'a': rng.normal(0.1, 1, size=2000),
            'b': rng.normal(2.0, 1, size=2000),
            'c': rng.normal(0.5, 1, size=2000),
        }
    )
    report = compute_csi(ref, cur, ['a', 'b', 'c'])
    out = csi_report_to_dataframe(report)
    assert list(out.columns) == [
        'feature',
        'csi',
        'classification',
        'n_reference',
        'n_current',
    ]
    assert out['csi'].is_monotonic_decreasing
    assert out['feature'].iloc[0] == 'b'  # largest shift


def test_csi_violations_only_flags_shifts() -> None:
    """csi_violations returns alert strings for minor + major shifts, ignores stable."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame(
        {
            'stable': rng.normal(0, 1, size=2000),
            'shifted_major': rng.normal(0, 1, size=2000),
        }
    )
    cur = pd.DataFrame(
        {
            'stable': rng.normal(0, 1, size=2000),
            'shifted_major': rng.normal(3.0, 1, size=2000),
        }
    )
    report = compute_csi(ref, cur, ['stable', 'shifted_major'])
    violations = csi_violations(report)
    assert len(violations) == 1
    assert 'MAJOR' in violations[0]
    assert 'shifted_major' in violations[0]


def test_handles_nan_values() -> None:
    """NaN values are excluded from CSI computation, don't crash."""
    ref = pd.DataFrame({'x': [1.0, 2.0, np.nan, 3.0, 4.0] * 200})
    cur = pd.DataFrame({'x': [1.0, 2.0, 3.0, 4.0, np.nan] * 200})
    report = compute_csi(ref, cur, ['x'])
    assert len(report.per_feature) == 1
    # Roughly identical distributions modulo NaN handling → stable
    assert report.per_feature[0].csi < 0.05


def test_overall_max_csi() -> None:
    """overall_max_csi reports the largest individual feature CSI."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame(
        {
            'a': rng.normal(0, 1, size=2000),
            'b': rng.normal(0, 1, size=2000),
        }
    )
    cur = pd.DataFrame(
        {
            'a': rng.normal(0, 1, size=2000),
            'b': rng.normal(4.0, 1, size=2000),
        }
    )
    report = compute_csi(ref, cur, ['a', 'b'])
    assert report.overall_max_csi == max(f.csi for f in report.per_feature)


def test_n_shifts_counts_correctly() -> None:
    """Report records the number of features in each shift bucket."""
    rng = np.random.default_rng(seed=42)
    ref = pd.DataFrame(
        {
            'a': rng.normal(0, 1, size=2000),
            'b': rng.normal(0, 1, size=2000),
            'c': rng.normal(0, 1, size=2000),
        }
    )
    cur = pd.DataFrame(
        {
            'a': rng.normal(0, 1, size=2000),  # stable
            'b': rng.normal(0.5, 1, size=2000),  # minor
            'c': rng.normal(3.0, 1, size=2000),  # major
        }
    )
    report = compute_csi(ref, cur, ['a', 'b', 'c'])
    assert report.n_major_shifts == 1
    assert report.n_minor_shifts == 1
