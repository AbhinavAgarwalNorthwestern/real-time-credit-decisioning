"""Unit tests for the WoE / IV scorecard utilities (Phase B Item S6).

Pure in-memory tests. No cluster, no cloud, no network. Deterministic via
explicit seeds.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.woe_scorecard import (
    WoeBin,
    WoeBinningResult,
    apply_woe,
    compute_woe,
    iv_strength_label,
    rank_features_by_iv,
    simulate_binary_target,
)


def test_iv_strength_label_buckets() -> None:
    """Siddiqi (2017) IV strength conventions."""
    assert iv_strength_label(0.001) == 'unpredictive'
    assert iv_strength_label(0.019) == 'unpredictive'
    assert iv_strength_label(0.02) == 'weak'
    assert iv_strength_label(0.099) == 'weak'
    assert iv_strength_label(0.10) == 'medium'
    assert iv_strength_label(0.29) == 'medium'
    assert iv_strength_label(0.30) == 'strong'
    assert iv_strength_label(0.49) == 'strong'
    assert iv_strength_label(0.50) == 'suspicious'
    assert iv_strength_label(2.0) == 'suspicious'


def test_compute_woe_perfect_separation() -> None:
    """A perfectly separating feature should produce a very high IV."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    feature = pd.Series(rng.normal(0, 1, size=n))
    # Perfect separation: target = 1 iff feature > 0
    target = pd.Series((feature > 0).astype(int))
    result = compute_woe(feature, target, n_bins=10, name='perfect')
    assert result.iv_total > 0.5, (
        f'expected IV > 0.5 for perfect separation, got {result.iv_total}'
    )
    assert iv_strength_label(result.iv_total) == 'suspicious'


def test_compute_woe_no_signal() -> None:
    """A random feature uncorrelated with target should have IV close to 0."""
    rng = np.random.default_rng(seed=42)
    n = 5000
    feature = pd.Series(rng.normal(0, 1, size=n))
    # Random target, independent of feature
    target = pd.Series(rng.binomial(1, 0.3, size=n))
    result = compute_woe(feature, target, n_bins=10, name='noise')
    assert result.iv_total < 0.10, (
        f'expected IV < 0.10 for noise, got {result.iv_total}'
    )


def test_compute_woe_discrete_feature_gets_unique_bins() -> None:
    """Features with n_unique <= n_bins get each unique value as a bin."""
    n_per = 200
    feature = pd.Series([0] * n_per + [1] * n_per + [2] * n_per + [3] * n_per)
    rng = np.random.default_rng(seed=42)
    # Higher value → higher event rate
    rates = {0: 0.05, 1: 0.15, 2: 0.30, 3: 0.50}
    target = pd.Series([rng.binomial(1, rates[v]) for v in feature])
    result = compute_woe(feature, target, n_bins=10, name='discrete')
    assert len(result.bins) == 4
    assert all(isinstance(b, WoeBin) for b in result.bins)
    assert result.n_unique_values == 4


def test_compute_woe_continuous_feature_uses_quantiles() -> None:
    """Continuous features with n_unique > n_bins get quantile binning."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    feature = pd.Series(rng.uniform(0, 100, size=n))
    target = pd.Series(rng.binomial(1, 0.2, size=n))
    result = compute_woe(feature, target, n_bins=10, name='continuous')
    assert len(result.bins) <= 10
    assert len(result.bins) >= 5
    assert result.n_unique_values == n


def test_compute_woe_monotonic_detection_positive() -> None:
    """A monotonically increasing event rate should be detected."""
    n_per = 500
    feature = pd.Series([1] * n_per + [2] * n_per + [3] * n_per + [4] * n_per)
    rng = np.random.default_rng(seed=42)
    rates = {1: 0.05, 2: 0.15, 3: 0.30, 4: 0.50}
    target = pd.Series([rng.binomial(1, rates[v]) for v in feature])
    result = compute_woe(feature, target, n_bins=10, name='mono_pos')
    assert result.monotonic_in_event_rate is True


def test_compute_woe_monotonic_detection_negative() -> None:
    """A monotonically decreasing event rate should also be detected."""
    n_per = 500
    feature = pd.Series([1] * n_per + [2] * n_per + [3] * n_per + [4] * n_per)
    rng = np.random.default_rng(seed=42)
    rates = {1: 0.50, 2: 0.30, 3: 0.15, 4: 0.05}
    target = pd.Series([rng.binomial(1, rates[v]) for v in feature])
    result = compute_woe(feature, target, n_bins=10, name='mono_neg')
    assert result.monotonic_in_event_rate is True


def test_compute_woe_non_monotonic_detection() -> None:
    """A U-shaped relationship is non-monotonic and should be flagged."""
    n_per = 500
    feature = pd.Series([1] * n_per + [2] * n_per + [3] * n_per + [4] * n_per)
    rng = np.random.default_rng(seed=42)
    rates = {1: 0.40, 2: 0.10, 3: 0.10, 4: 0.40}
    target = pd.Series([rng.binomial(1, rates[v]) for v in feature])
    result = compute_woe(feature, target, n_bins=10, name='u_shape')
    assert result.monotonic_in_event_rate is False


def test_apply_woe_assigns_correct_woe_per_bin() -> None:
    """apply_woe should map each row to its bin's WoE."""
    n_per = 200
    feature = pd.Series([1] * n_per + [2] * n_per + [3] * n_per)
    rng = np.random.default_rng(seed=42)
    rates = {1: 0.05, 2: 0.30, 3: 0.60}
    target = pd.Series([rng.binomial(1, rates[v]) for v in feature])
    result = compute_woe(feature, target, n_bins=10, name='apply_test')

    encoded = apply_woe(feature, result)
    assert len(encoded) == len(feature)
    # Every value should map to one of the bin WoEs
    valid_woes = {b.woe for b in result.bins}
    for w in encoded.unique():
        assert w in valid_woes or any(abs(w - bw) < 1e-9 for bw in valid_woes)


def test_apply_woe_out_of_range_uses_edge_bin() -> None:
    """Values outside the fitted range should fall back to the nearest bin's WoE."""
    feature = pd.Series([1, 2, 3, 4, 5] * 50)
    target = pd.Series([0, 0, 1, 1, 1] * 50)
    result = compute_woe(feature, target, n_bins=5, name='oor')

    # Encode a value below the fitted minimum (1)
    encoded = apply_woe(pd.Series([-10.0]), result)
    assert not encoded.isna().any()
    assert encoded.iloc[0] == result.bins[0].woe

    # Encode a value above the fitted maximum (5)
    encoded_high = apply_woe(pd.Series([99.0]), result)
    assert encoded_high.iloc[0] == result.bins[-1].woe


def test_rank_features_by_iv_returns_descending() -> None:
    """The ranking table should be sorted by IV descending."""
    rng = np.random.default_rng(seed=42)
    n = 2000
    df = pd.DataFrame(
        {
            'strong_feature': rng.normal(0, 1, size=n),
            'weak_feature': rng.normal(0, 1, size=n),
            'noise': rng.normal(0, 1, size=n),
        }
    )
    # Build a target that's strongly correlated with strong_feature only
    target = pd.Series(
        ((df['strong_feature'] + rng.normal(0, 0.1, size=n)) > 0).astype(int)
    )
    ranking = rank_features_by_iv(
        df, target, ['strong_feature', 'weak_feature', 'noise']
    )
    assert list(ranking['feature'])[0] == 'strong_feature'
    assert ranking['iv'].is_monotonic_decreasing


def test_simulate_binary_target_determinism() -> None:
    """Same seed → identical binary labels."""
    p = pd.Series([0.1, 0.5, 0.9, 0.3])
    y1 = simulate_binary_target(p, seed=42)
    y2 = simulate_binary_target(p, seed=42)
    assert (y1 == y2).all()


def test_simulate_binary_target_rate_matches_input() -> None:
    """Bernoulli realizations should track the input probability over enough draws."""
    n = 10000
    rng = np.random.default_rng(seed=7)
    p = pd.Series(rng.uniform(0, 1, size=n))
    y = simulate_binary_target(p, seed=42)
    realized_rate = y.mean()
    expected_rate = p.mean()
    assert abs(realized_rate - expected_rate) < 0.02


def test_compute_woe_iv_total_equals_sum_of_contributions() -> None:
    """The reported iv_total must equal the sum of per-bin iv_contribution."""
    rng = np.random.default_rng(seed=42)
    n = 1000
    feature = pd.Series(rng.normal(0, 1, size=n))
    target = pd.Series((feature > 0.5).astype(int))
    result = compute_woe(feature, target, n_bins=10, name='consistency')
    summed = sum(b.iv_contribution for b in result.bins)
    assert abs(result.iv_total - summed) < 1e-9


def test_compute_woe_target_rate_recorded() -> None:
    """The result should expose the overall target event rate."""
    n = 500
    feature = pd.Series(np.arange(n).astype(float))
    target = pd.Series([1] * 100 + [0] * 400)  # 20% event rate
    result = compute_woe(feature, target, n_bins=5, name='rate')
    assert abs(result.target_event_rate - 0.2) < 0.01


def test_returns_typed_result() -> None:
    """compute_woe must return a WoeBinningResult."""
    feature = pd.Series([1, 2, 3, 4, 5] * 50)
    target = pd.Series([0, 0, 1, 1, 1] * 50)
    result = compute_woe(feature, target, n_bins=5, name='typed')
    assert isinstance(result, WoeBinningResult)
