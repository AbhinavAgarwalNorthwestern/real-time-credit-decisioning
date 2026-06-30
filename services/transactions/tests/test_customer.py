"""Unit tests for the Customer cohort generator.

Covers the 5 customer-level attributes (credit_score, annual_income,
account_tenure_months, n_products, prev_delinquency_count) and the causal
adjustments they impose on the ground-truth response parameters.

Tests run on synthetic in-memory cohorts only — no cluster, no cloud, no
network. Same suite runs identically against any deployment target.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np
from transactions.customer import generate_cohort


def test_customer_has_5_new_fields() -> None:
    cohort = generate_cohort(size=10, seed=42)
    customer = cohort[0]
    for fld in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert hasattr(customer, fld), f'Customer is missing field: {fld}'


def test_attribute_value_ranges() -> None:
    """All 5 attributes must be in their declared valid ranges."""
    cohort = generate_cohort(size=200, seed=7)
    for c in cohort:
        assert 300 <= c.credit_score <= 850
        assert c.annual_income > 0
        assert 1 <= c.account_tenure_months <= 360
        assert 1 <= c.n_products <= 10
        assert 0 <= c.prev_delinquency_count <= 20


def test_seed_determinism() -> None:
    """Same seed must produce identical cohorts including the 5 new attrs."""
    c1 = generate_cohort(size=50, seed=123)
    c2 = generate_cohort(size=50, seed=123)
    assert len(c1) == len(c2) == 50
    for a, b in zip(c1, c2, strict=True):
        assert a == b, f'Cohort drift at {a.customer_id}: {a} != {b}'


def test_segment_separation_credit_score() -> None:
    """LOW_RISK_TENURED (seg 0) credit_score >> HIGH_RISK_NEW (seg 5)."""
    cohort = generate_cohort(size=3000, seed=42)
    seg0 = [c.credit_score for c in cohort if c.segment_id == 0]
    seg5 = [c.credit_score for c in cohort if c.segment_id == 5]
    assert len(seg0) > 100 and len(seg5) > 100, 'too few samples per segment'
    # Segment 0 mean ~760; segment 5 mean ~570 → gap should exceed 100 points
    assert np.mean(seg0) - np.mean(seg5) > 100


def test_segment_separation_income() -> None:
    """LOW_RISK_TENURED has higher annual income than HIGH_RISK_NEW."""
    cohort = generate_cohort(size=3000, seed=42)
    seg0_inc = [c.annual_income for c in cohort if c.segment_id == 0]
    seg5_inc = [c.annual_income for c in cohort if c.segment_id == 5]
    assert np.mean(seg0_inc) > np.mean(seg5_inc) * 1.5


def test_segment_separation_tenure() -> None:
    """Tenured segments have much longer account history than new ones."""
    cohort = generate_cohort(size=3000, seed=42)
    seg0_tenure = [c.account_tenure_months for c in cohort if c.segment_id == 0]
    seg1_tenure = [c.account_tenure_months for c in cohort if c.segment_id == 1]
    # Seg 0: μ=96mo, Seg 1: μ=12mo
    assert np.mean(seg0_tenure) > np.mean(seg1_tenure) * 3


def test_causal_effect_credit_score_on_p_default() -> None:
    """Higher credit score → lower true_p_default within a segment."""
    # Use a large cohort so the small causal signal beats sampling noise
    cohort = generate_cohort(size=10000, seed=42)
    # Use segment 4 (HIGH_RISK_TENURED) — wide credit_score sigma (50) gives signal
    seg4 = [c for c in cohort if c.segment_id == 4]
    assert len(seg4) > 500
    scores = np.array([c.credit_score for c in seg4])
    defaults = np.array([c.true_p_default for c in seg4])
    corr = float(np.corrcoef(scores, defaults)[0, 1])
    # Causal direction must be negative; magnitude is small (segment dominates)
    assert corr < 0, f'expected negative corr, got {corr}'


def test_causal_effect_income_on_delta_spend() -> None:
    """Higher annual_income → higher true_delta_spend (pooled across segments)."""
    cohort = generate_cohort(size=3000, seed=42)
    incomes = np.array([np.log(c.annual_income) for c in cohort])
    spends = np.array([c.true_delta_spend_if_accept for c in cohort])
    corr = float(np.corrcoef(incomes, spends)[0, 1])
    assert corr > 0.3, f'expected strong positive corr, got {corr}'


def test_p_accept_bounded() -> None:
    """After causal adjustments, p_accept must remain in [0.01, 0.95]."""
    cohort = generate_cohort(size=2000, seed=42)
    for c in cohort:
        assert 0.01 <= c.true_p_accept_cli <= 0.95


def test_p_default_bounded() -> None:
    """After causal adjustments, p_default must remain in [0.001, 0.60]."""
    cohort = generate_cohort(size=2000, seed=42)
    for c in cohort:
        assert 0.001 <= c.true_p_default <= 0.60


def test_delta_spend_non_negative() -> None:
    """delta_spend must never go negative even after income_z adjustment."""
    cohort = generate_cohort(size=2000, seed=42)
    for c in cohort:
        assert c.true_delta_spend_if_accept >= 0.0


def test_attribute_count_matches_population_share() -> None:
    """Customer-attribute draws must respect segment population shares."""
    cohort = generate_cohort(size=10000, seed=42)
    counts: defaultdict[int, int] = defaultdict(int)
    for c in cohort:
        counts[c.segment_id] += 1
    # Expected shares per dgp_design.md: 0.25, 0.15, 0.20, 0.15, 0.15, 0.10
    expected = {0: 2500, 1: 1500, 2: 2000, 3: 1500, 4: 1500, 5: 1000}
    for seg_id, exp_count in expected.items():
        got = counts[seg_id]
        # 5% tolerance band
        assert abs(got - exp_count) < exp_count * 0.05, (
            f'segment {seg_id} count {got} far from expected {exp_count}'
        )
