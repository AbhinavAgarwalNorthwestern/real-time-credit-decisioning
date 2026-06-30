"""Unit tests for the calibration library (Phase B Item S1).

Tests use synthetic data with known ground-truth calibration. All in-memory;
no cluster, no cloud, no network.
"""

from __future__ import annotations

import numpy as np
from training_flow.calibration import (
    CalibrationBin,
    CalibrationResult,
    apply_isotonic,
    brier_score,
    calibration_curve,
    calibration_summary,
    fit_isotonic_recalibrator,
    hosmer_lemeshow_test,
    segment_calibration,
)


def test_brier_score_perfect_predictor() -> None:
    """A perfect predictor (p=1 for events, p=0 for non-events) has Brier = 0."""
    y_true = np.array([1, 0, 1, 0, 1])
    y_pred = np.array([1.0, 0.0, 1.0, 0.0, 1.0])
    assert brier_score(y_true, y_pred) == 0.0


def test_brier_score_worst_predictor() -> None:
    """A worst-case predictor (inverted) has Brier = 1."""
    y_true = np.array([1, 0, 1, 0])
    y_pred = np.array([0.0, 1.0, 0.0, 1.0])
    assert brier_score(y_true, y_pred) == 1.0


def test_brier_score_base_rate_predictor() -> None:
    """Predicting the base rate everywhere → Brier = p(1-p)."""
    rng = np.random.default_rng(seed=42)
    n = 10000
    base_rate = 0.3
    y_true = rng.binomial(1, base_rate, size=n).astype(int)
    y_pred = np.full(n, base_rate)
    expected = base_rate * (1 - base_rate)
    bs = brier_score(y_true, y_pred)
    assert abs(bs - expected) < 0.01


def test_hosmer_lemeshow_well_calibrated_high_pvalue() -> None:
    """Well-calibrated model should produce a HIGH HL p-value."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    y_pred = rng.uniform(0.0, 1.0, size=n)
    # True outcomes drawn from the SAME distribution as predictions
    y_true = rng.binomial(1, y_pred, size=n).astype(int)
    hl, p, df = hosmer_lemeshow_test(y_true, y_pred, n_bins=10)
    assert p > 0.05, f'well-calibrated should have p>0.05, got p={p}, HL={hl}'
    assert df == 8  # n_bins - 2


def test_hosmer_lemeshow_miscalibrated_low_pvalue() -> None:
    """Severely miscalibrated model should produce a LOW HL p-value."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    y_pred = rng.uniform(0.0, 1.0, size=n)
    # Predictions say ~50% but actual rate is fixed 10% — badly miscalibrated
    y_true = rng.binomial(1, 0.10, size=n).astype(int)
    hl, p, _df = hosmer_lemeshow_test(y_true, y_pred, n_bins=10)
    assert p < 0.01, f'miscalibrated should have p<0.01, got p={p}, HL={hl}'


def test_calibration_curve_perfect_alignment() -> None:
    """Mean predicted == mean actual per bin → calibration error ~0."""
    rng = np.random.default_rng(seed=42)
    n = 20000
    y_pred = rng.uniform(0.0, 1.0, size=n)
    y_true = rng.binomial(1, y_pred, size=n).astype(int)
    bins = calibration_curve(y_true, y_pred, n_bins=10)
    assert len(bins) == 10
    for b in bins:
        assert abs(b.mean_predicted - b.mean_actual) < 0.05  # within sampling tolerance


def test_calibration_curve_returns_sorted_bins() -> None:
    """Bins must be sorted by lower_edge ascending."""
    rng = np.random.default_rng(seed=42)
    y_pred = rng.uniform(0.0, 1.0, size=1000)
    y_true = rng.binomial(1, y_pred).astype(int)
    bins = calibration_curve(y_true, y_pred, n_bins=10)
    for i in range(len(bins) - 1):
        assert bins[i].lower_edge <= bins[i + 1].lower_edge


def test_calibration_summary_combines_everything() -> None:
    """calibration_summary returns a CalibrationResult with all fields populated."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    y_pred = rng.uniform(0.0, 1.0, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    result = calibration_summary(y_true, y_pred, n_bins=10)
    assert isinstance(result, CalibrationResult)
    assert result.n_observations == n
    assert result.n_bins == 10
    assert isinstance(result.brier_score, float)
    assert isinstance(result.hl_statistic, float)
    assert 0.0 <= result.hl_p_value <= 1.0
    assert result.hl_df == 8
    assert isinstance(result.bins[0], CalibrationBin)
    assert result.weighted_calibration_error >= 0


def test_isotonic_recalibrator_preserves_rank_ordering() -> None:
    """Isotonic regression is monotonic by construction; rank order must be
    preserved (Spearman ρ = 1 between raw and recalibrated predictions).
    """
    from scipy.stats import spearmanr

    rng = np.random.default_rng(seed=42)
    n = 5000
    true_p = rng.uniform(0.0, 1.0, size=n)
    y = rng.binomial(1, true_p).astype(int)
    y_pred_raw = true_p**2

    model = fit_isotonic_recalibrator(y_pred_raw, y)
    y_recal = apply_isotonic(y_pred_raw, model)
    rho, _p = spearmanr(y_pred_raw, y_recal)
    assert rho > 0.99, f'isotonic must preserve rank order, got spearman={rho}'


def test_isotonic_predict_outputs_in_unit_interval() -> None:
    """Isotonic-recalibrated probabilities must remain in [0, 1]."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    y_true = rng.binomial(1, 0.3, size=n).astype(int)
    y_pred = rng.uniform(0.0, 1.0, size=n)
    model = fit_isotonic_recalibrator(y_pred, y_true)
    recal = apply_isotonic(y_pred, model)
    assert (recal >= 0.0).all() and (recal <= 1.0).all()


def test_isotonic_handles_out_of_range_inputs() -> None:
    """Isotonic must clip predictions outside the training range."""
    rng = np.random.default_rng(seed=42)
    y_true = rng.binomial(1, 0.3, size=500).astype(int)
    y_pred = rng.uniform(0.1, 0.9, size=500)  # narrower range
    model = fit_isotonic_recalibrator(y_pred, y_true)
    # Predict at endpoints OUTSIDE the fitted range
    extreme = np.array([-0.5, 0.0, 1.0, 1.5])
    recal = apply_isotonic(extreme, model)
    assert (recal >= 0.0).all() and (recal <= 1.0).all()


def test_segment_calibration_returns_per_segment_result() -> None:
    """segment_calibration returns one CalibrationResult per segment."""
    rng = np.random.default_rng(seed=42)
    n = 6000
    y_pred = rng.uniform(0.0, 1.0, size=n)
    y_true = rng.binomial(1, y_pred).astype(int)
    segments = rng.integers(0, 6, size=n)  # 6 segments
    results = segment_calibration(y_true, y_pred, segments, n_bins=10)
    assert isinstance(results, dict)
    assert set(results.keys()).issubset(set(range(6)))
    for r in results.values():
        assert isinstance(r, CalibrationResult)


def test_segment_calibration_skips_tiny_segments() -> None:
    """Segments with fewer than n_bins observations are skipped (no HL test possible)."""
    n = 100
    y_true = np.array([0, 1] * (n // 2), dtype=int)
    y_pred = np.linspace(0.1, 0.9, n)
    # Segment 0 has 95 obs, segment 1 has 5 obs (below n_bins=10)
    segments = np.array([0] * 95 + [1] * 5)
    results = segment_calibration(y_true, y_pred, segments, n_bins=10)
    assert 0 in results
    assert 1 not in results  # too small


def test_brier_length_mismatch_raises() -> None:
    """Mismatched arrays must raise."""
    try:
        brier_score(np.array([0, 1]), np.array([0.5, 0.5, 0.5]))
        raise AssertionError('should have raised')
    except ValueError:
        pass


def test_calibration_curve_n_bins_respected() -> None:
    """Requested n_bins should produce that many bins (up to qcut deduplication)."""
    rng = np.random.default_rng(seed=42)
    y_pred = rng.uniform(0.0, 1.0, size=5000)
    y_true = rng.binomial(1, y_pred).astype(int)
    bins_5 = calibration_curve(y_true, y_pred, n_bins=5)
    bins_20 = calibration_curve(y_true, y_pred, n_bins=20)
    assert len(bins_5) == 5
    assert len(bins_20) == 20
