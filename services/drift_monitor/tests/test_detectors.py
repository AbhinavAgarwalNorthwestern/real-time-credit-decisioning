"""Unit tests for the 7 drift detectors (ADR 012)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from drift_monitor.detectors import (
    ADWINDetector,
    DriftSignal,
    JSDivergenceDetector,
    KSDetector,
    PerformanceDriftDetector,
    PerSegmentDriftMonitor,
    PSIDetector,
    SchemaDriftDetector,
)

SEED = 42


# ── PSI ───────────────────────────────────────────────────────────


def test_psi_no_drift_same_distribution() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 1000)
    cur = rng.normal(0, 1, 1000)
    sig = PSIDetector().detect(ref, cur, 'velocity_5m')
    assert not sig.is_drifting
    assert sig.statistic < 0.2


def test_psi_detects_mean_shift() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 1000)
    cur = rng.normal(3, 1, 1000)
    sig = PSIDetector().detect(ref, cur, 'velocity_5m')
    assert sig.is_drifting
    assert sig.statistic > 0.2


def test_psi_empty_array() -> None:
    sig = PSIDetector().detect(np.array([]), np.array([1.0, 2.0]), 'x')
    assert not sig.is_drifting
    assert sig.statistic == 0.0


def test_psi_handles_nan() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 500)
    cur = np.concatenate([rng.normal(0, 1, 400), np.array([np.nan] * 100)])
    sig = PSIDetector().detect(ref, cur, 'x')
    assert isinstance(sig, DriftSignal)


# ── KS ────────────────────────────────────────────────────────────


def test_ks_no_drift_same_distribution() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 500)
    cur = rng.normal(0, 1, 500)
    sig = KSDetector().detect(ref, cur, 'total_spend_1h')
    assert not sig.is_drifting
    assert sig.statistic > 0.05


def test_ks_detects_distribution_shift() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 500)
    cur = rng.exponential(2, 500)
    sig = KSDetector().detect(ref, cur, 'total_spend_1h')
    assert sig.is_drifting
    assert sig.statistic < 0.05


def test_ks_insufficient_samples() -> None:
    sig = KSDetector().detect(np.arange(10.0), np.arange(10.0), 'x')
    assert not sig.is_drifting
    assert sig.statistic == 1.0


# ── ADWIN ─────────────────────────────────────────────────────────


def test_adwin_no_drift_stationary() -> None:
    rng = np.random.default_rng(SEED)
    det = ADWINDetector(delta=0.002)
    drifted = False
    for v in rng.normal(0, 1, 100):
        if det.push(float(v)):
            drifted = True
    assert not drifted


def test_adwin_detects_level_shift() -> None:
    det = ADWINDetector(delta=0.002)
    for v in [0.0] * 50:
        det.push(v)
    detected = False
    for v in [5.0] * 50:
        if det.push(v):
            detected = True
    assert detected


def test_adwin_resets_window_on_detection() -> None:
    det = ADWINDetector(delta=0.002)
    for v in [0.0] * 50 + [5.0] * 50:
        det.push(v)
    assert len(det._window) < 100


# ── JS Divergence ─────────────────────────────────────────────────


def test_js_no_drift_same_distribution() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 500)
    cur = rng.normal(0, 1, 500)
    sig = JSDivergenceDetector().detect(ref, cur, 'uplift_score')
    assert not sig.is_drifting


def test_js_detects_shift() -> None:
    rng = np.random.default_rng(SEED)
    ref = rng.normal(0, 1, 500)
    cur = rng.normal(5, 0.5, 500)
    sig = JSDivergenceDetector().detect(ref, cur, 'uplift_score')
    assert sig.is_drifting
    assert sig.statistic > 0.1


def test_js_insufficient_samples() -> None:
    sig = JSDivergenceDetector().detect(np.arange(5.0), np.arange(5.0), 'x')
    assert not sig.is_drifting
    assert sig.statistic == 0.0


# ── Performance Drift ─────────────────────────────────────────────


def test_perf_no_gap() -> None:
    rng = np.random.default_rng(SEED)
    pred = rng.normal(100, 5, 200)
    sig = PerformanceDriftDetector().detect_gap(pred, pred, 'overall')
    assert not sig.is_drifting


def test_perf_detects_large_gap() -> None:
    rng = np.random.default_rng(SEED)
    pred = rng.normal(100, 5, 200)
    real = rng.normal(50, 5, 200)
    sig = PerformanceDriftDetector().detect_gap(pred, real, 'overall')
    assert sig.is_drifting
    assert sig.statistic > 0.15


def test_perf_insufficient_data() -> None:
    sig = PerformanceDriftDetector().detect_gap(np.arange(10.0), np.arange(10.0), 'x')
    assert not sig.is_drifting


def test_perf_near_zero_predicted() -> None:
    pred = np.full(100, 1e-8)
    real = np.full(100, 1e-8)
    sig = PerformanceDriftDetector().detect_gap(pred, real, 'x')
    assert not sig.is_drifting


# ── Schema Drift ─────────────────────────────────────────────────


def test_schema_no_increase() -> None:
    sig = SchemaDriftDetector().detect_missingness(0.01, 0.01, 'velocity_5m')
    assert not sig.is_drifting


def test_schema_detects_missingness_spike() -> None:
    sig = SchemaDriftDetector().detect_missingness(0.01, 0.10, 'velocity_5m')
    assert sig.is_drifting
    assert sig.statistic == pytest.approx(0.09, abs=1e-6)


def test_schema_decrease_is_ok() -> None:
    sig = SchemaDriftDetector().detect_missingness(0.10, 0.02, 'x')
    assert not sig.is_drifting


# ── Per-Segment Drift Monitor ────────────────────────────────────


def test_per_segment_no_drift() -> None:
    rng = np.random.default_rng(SEED)
    n = 500
    ref = pd.DataFrame(
        {
            'segment_id': np.repeat([0, 1], n // 2),
            'velocity_5m': rng.normal(0, 1, n),
        }
    )
    cur = pd.DataFrame(
        {
            'segment_id': np.repeat([0, 1], n // 2),
            'velocity_5m': rng.normal(0, 1, n),
        }
    )
    mon = PerSegmentDriftMonitor(PSIDetector())
    sigs = mon.detect_per_segment(ref, cur, 'velocity_5m')
    assert len(sigs) == 2
    assert all(not s.is_drifting for s in sigs)


def test_per_segment_drift_in_one_segment() -> None:
    rng = np.random.default_rng(SEED)
    n = 500
    ref = pd.DataFrame(
        {
            'segment_id': np.repeat([0, 1], n // 2),
            'velocity_5m': rng.normal(0, 1, n),
        }
    )
    cur_vals = np.concatenate(
        [
            rng.normal(0, 1, n // 2),
            rng.normal(5, 1, n // 2),
        ]
    )
    cur = pd.DataFrame(
        {
            'segment_id': np.repeat([0, 1], n // 2),
            'velocity_5m': cur_vals,
        }
    )
    mon = PerSegmentDriftMonitor(PSIDetector())
    sigs = mon.detect_per_segment(ref, cur, 'velocity_5m')
    seg0_drift = [s for s in sigs if 'segment_0' in s.feature][0]
    seg1_drift = [s for s in sigs if 'segment_1' in s.feature][0]
    assert not seg0_drift.is_drifting
    assert seg1_drift.is_drifting


def test_per_segment_labels_include_feature_and_segment() -> None:
    rng = np.random.default_rng(SEED)
    df = pd.DataFrame(
        {
            'segment_id': [0, 0, 1, 1] * 25,
            'feat': rng.normal(0, 1, 100),
        }
    )
    mon = PerSegmentDriftMonitor(PSIDetector())
    sigs = mon.detect_per_segment(df, df, 'feat')
    for s in sigs:
        assert s.feature.startswith('feat@segment_')
