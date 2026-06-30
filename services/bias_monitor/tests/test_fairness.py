"""Unit tests for the fairness library.

Verifies the math (DP / EOdds / PP) against known constructed examples
and the contract (thresholds, violation reporting, group iteration).
"""

from __future__ import annotations

import numpy as np
from bias_monitor.fairness import (
    EIGHTY_PCT_RULE_FLOOR,
    FairnessReport,
    GroupMetrics,
    compute_fairness_report,
    credit_score_buckets,
    report_summary_lines,
)


def _equal_groups_perfect_calibration() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two groups, identical base rates, identical model behavior → perfect fairness."""
    rng = np.random.default_rng(seed=42)
    n_per = 1000
    y = np.concatenate(
        [
            rng.binomial(1, 0.3, size=n_per).astype(int),
            rng.binomial(1, 0.3, size=n_per).astype(int),
        ]
    )
    # Predict the actual label perfectly
    p = y.astype(float)
    g = np.array(['A'] * n_per + ['B'] * n_per)
    return y, p, g


def _heavily_biased_groups() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Group A: high positive prediction rate. Group B: low. Should fail DP."""
    rng = np.random.default_rng(seed=42)
    n_per = 1000
    y_a = rng.binomial(1, 0.4, size=n_per).astype(int)
    y_b = rng.binomial(1, 0.4, size=n_per).astype(int)
    # Predict positive for 90% of A, only 10% of B (deliberately biased)
    p_a = rng.binomial(1, 0.9, size=n_per).astype(float)
    p_b = rng.binomial(1, 0.1, size=n_per).astype(float)
    return (
        np.concatenate([y_a, y_b]),
        np.concatenate([p_a, p_b]),
        np.array(['A'] * n_per + ['B'] * n_per),
    )


def test_compute_fairness_report_perfect_calibration() -> None:
    """When groups are identical, all three criteria should pass."""
    y, p, g = _equal_groups_perfect_calibration()
    report = compute_fairness_report(y, p, g, group_dimension='ab_group')
    assert isinstance(report, FairnessReport)
    assert report.group_dimension == 'ab_group'
    assert len(report.per_group) == 2
    assert report.demographic_parity_pass
    assert report.equalized_odds_pass
    assert report.predictive_parity_pass
    assert report.violations == []


def test_compute_fairness_report_demographic_parity_violation() -> None:
    """A model with very different positive-prediction rates per group fails DP."""
    y, p, g = _heavily_biased_groups()
    report = compute_fairness_report(y, p, g)
    assert not report.demographic_parity_pass
    assert report.demographic_parity_ratio < EIGHTY_PCT_RULE_FLOOR
    assert any('demographic_parity' in v for v in report.violations)


def test_compute_fairness_report_per_group_metrics_correct() -> None:
    """Per-group TPR / FPR / PPV are computed from the confusion matrix."""
    # Construct an exact case
    # Group A: 10 actual positives, 10 actual negatives
    # Predict positive for 8 actual positives + 2 actual negatives → TPR=0.8, FPR=0.2, PPV=0.8
    y = np.array([1] * 10 + [0] * 10)
    p = np.array(([1] * 8 + [0] * 2) + ([1] * 2 + [0] * 8), dtype=float)
    g = np.array(['A'] * 20)
    report = compute_fairness_report(y, p, g)
    assert len(report.per_group) == 1
    m = report.per_group[0]
    assert m.n == 20
    assert m.n_positive_actual == 10
    assert m.n_positive_predicted == 10
    assert abs(m.true_positive_rate - 0.8) < 1e-9
    assert abs(m.false_positive_rate - 0.2) < 1e-9
    assert abs(m.positive_predictive_value - 0.8) < 1e-9
    assert abs(m.positive_pred_rate - 0.5) < 1e-9


def test_compute_fairness_report_dp_ratio_eighty_pct() -> None:
    """A 5:4 prediction-rate split should give DP ratio = 0.8 = exactly at the floor."""
    # 100 predictions in each group; A predicts positive 50 times (rate 0.5),
    # B predicts positive 40 times (rate 0.4) → ratio = 0.4/0.5 = 0.8
    n = 100
    y_a = np.zeros(n, dtype=int)
    y_b = np.zeros(n, dtype=int)
    p_a = np.array([1.0] * 50 + [0.0] * 50)
    p_b = np.array([1.0] * 40 + [0.0] * 60)
    g_a = np.array(['A'] * n)
    g_b = np.array(['B'] * n)
    report = compute_fairness_report(
        np.concatenate([y_a, y_b]),
        np.concatenate([p_a, p_b]),
        np.concatenate([g_a, g_b]),
    )
    assert abs(report.demographic_parity_ratio - 0.8) < 1e-9


def test_compute_fairness_report_handles_three_groups() -> None:
    """Report should iterate cleanly over >2 groups."""
    rng = np.random.default_rng(seed=42)
    n_per = 500
    parts_y = []
    parts_p = []
    parts_g = []
    for letter in ['A', 'B', 'C']:
        parts_y.append(rng.binomial(1, 0.3, size=n_per).astype(int))
        parts_p.append(rng.binomial(1, 0.3, size=n_per).astype(float))
        parts_g.append(np.array([letter] * n_per))
    report = compute_fairness_report(
        np.concatenate(parts_y),
        np.concatenate(parts_p),
        np.concatenate(parts_g),
    )
    assert len(report.per_group) == 3
    assert sorted([m.group_id for m in report.per_group]) == ['A', 'B', 'C']


def test_compute_fairness_report_probability_thresholding() -> None:
    """Probabilities should be binarized at the configured threshold."""
    y = np.array([1, 1, 0, 0])
    p = np.array([0.7, 0.6, 0.4, 0.3])
    g = np.array(['A', 'A', 'A', 'A'])
    report = compute_fairness_report(y, p, g, threshold=0.5)
    assert report.per_group[0].n_positive_predicted == 2


def test_compute_fairness_report_length_mismatch_raises() -> None:
    """Mismatched array lengths should produce a useful error."""
    try:
        compute_fairness_report(np.array([0, 1]), np.array([0.5]), np.array(['A', 'B']))
        raise AssertionError('should have raised')
    except ValueError as e:
        assert 'length mismatch' in str(e)


def test_compute_fairness_report_empty_group_handled() -> None:
    """A group with no positive actuals returns None for TPR (no division by zero)."""
    y = np.array([0, 0, 0, 0])
    p = np.array([0.6, 0.7, 0.4, 0.3])
    g = np.array(['A', 'A', 'A', 'A'])
    report = compute_fairness_report(y, p, g)
    m = report.per_group[0]
    assert m.true_positive_rate is None


def test_credit_score_buckets_returns_five_levels() -> None:
    """Default bucketing produces 5 levels for fair-lending tiering."""
    rng = np.random.default_rng(seed=42)
    scores = rng.uniform(500, 850, size=10000)
    buckets = credit_score_buckets(scores, n_buckets=5)
    distinct = set(buckets.tolist())
    assert len(distinct) == 5


def test_credit_score_buckets_monotonic_in_input() -> None:
    """Higher scores get higher bucket ids."""
    scores = np.array([500.0, 550, 600, 650, 700, 750, 800, 850] * 10)
    buckets = credit_score_buckets(scores, n_buckets=4)
    df_pairs = sorted(zip(scores.tolist(), buckets.tolist(), strict=True))
    # Adjacent pairs: lower score → bucket id <= higher score's bucket id
    for i in range(len(df_pairs) - 1):
        assert df_pairs[i][1] <= df_pairs[i + 1][1]


def test_violations_format_for_alerting() -> None:
    """Violations are formatted as parseable strings for downstream alerting."""
    y, p, g = _heavily_biased_groups()
    report = compute_fairness_report(y, p, g)
    assert any(v.startswith('demographic_parity:') for v in report.violations)


def test_report_summary_lines_renders() -> None:
    """Summary lines render without errors and contain key metrics."""
    y, p, g = _equal_groups_perfect_calibration()
    report = compute_fairness_report(y, p, g)
    lines = report_summary_lines(report)
    joined = '\n'.join(lines)
    assert 'Fairness report' in joined
    assert 'Demographic parity ratio' in joined
    assert 'No violations' in joined


def test_group_metrics_records_counts() -> None:
    """GroupMetrics carries the raw counts for downstream Prometheus export."""
    y = np.array([1, 1, 1, 0, 0, 0])
    p = np.array([1, 1, 0, 1, 0, 0], dtype=float)
    g = np.array(['X'] * 6)
    report = compute_fairness_report(y, p, g)
    m = report.per_group[0]
    assert isinstance(m, GroupMetrics)
    assert m.n == 6
    assert m.n_positive_actual == 3
    assert m.n_positive_predicted == 3
    assert m.true_positive_rate is not None
    assert m.false_positive_rate is not None


def test_high_tolerance_makes_minor_imbalance_pass() -> None:
    """Loosening the threshold should allow modest imbalance to pass."""
    y, p, g = _heavily_biased_groups()
    strict = compute_fairness_report(y, p, g, eighty_pct_rule_floor=0.8)
    loose = compute_fairness_report(y, p, g, eighty_pct_rule_floor=0.1)
    assert not strict.demographic_parity_pass
    assert loose.demographic_parity_pass


def test_eight_pct_rule_default_value() -> None:
    """The 80% rule floor must default to 0.80 per EEOC convention."""
    assert EIGHTY_PCT_RULE_FLOOR == 0.80
