"""Unit tests for PD × LGD × EAD loss decomposition (Phase B Item S4)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.loss_forecasting import (
    DEFAULT_DRAW_FACTOR,
    DEFAULT_LGD_RETAIL_CARD,
    PortfolioLossResult,
    compute_ead,
    expected_loss_per_account,
    loss_components_frame,
    loss_rate_by_segment,
    portfolio_summary,
    unexpected_loss_per_account,
)


def test_compute_ead_zero_available_credit() -> None:
    """When balance == limit, EAD = balance (no draw possible)."""
    balance = np.array([1000.0, 2000.0])
    limit = np.array([1000.0, 2000.0])
    ead = compute_ead(balance, limit, draw_factor=0.60)
    np.testing.assert_array_equal(ead, balance)


def test_compute_ead_zero_balance() -> None:
    """When balance == 0 and limit > 0, EAD = draw_factor × limit."""
    balance = np.array([0.0, 0.0])
    limit = np.array([1000.0, 5000.0])
    ead = compute_ead(balance, limit, draw_factor=0.5)
    np.testing.assert_array_equal(ead, np.array([500.0, 2500.0]))


def test_compute_ead_partial_utilization() -> None:
    """EAD interpolates correctly between balance and limit."""
    balance = np.array([300.0])
    limit = np.array([1000.0])
    ead = compute_ead(balance, limit, draw_factor=0.60)
    # EAD = 300 + 0.60 * (1000 - 300) = 300 + 420 = 720
    np.testing.assert_array_almost_equal(ead, np.array([720.0]))


def test_compute_ead_balance_above_limit_clamps_available_to_zero() -> None:
    """If a customer is over-limit, available credit is treated as 0."""
    balance = np.array([1500.0])
    limit = np.array([1000.0])
    ead = compute_ead(balance, limit, draw_factor=0.60)
    np.testing.assert_array_equal(ead, balance)


def test_expected_loss_formula() -> None:
    """EL = PD × LGD × EAD per Basel III."""
    pd_arr = np.array([0.10, 0.05])
    lgd = 0.80
    ead = np.array([1000.0, 2000.0])
    el = expected_loss_per_account(pd_arr, lgd, ead)
    # 0.10 * 0.80 * 1000 = 80; 0.05 * 0.80 * 2000 = 80
    np.testing.assert_array_almost_equal(el, np.array([80.0, 80.0]))


def test_expected_loss_per_account_lgd_array() -> None:
    """LGD can vary per-account."""
    pd_arr = np.array([0.10, 0.10])
    lgd = np.array([0.5, 1.0])
    ead = np.array([1000.0, 1000.0])
    el = expected_loss_per_account(pd_arr, lgd, ead)
    np.testing.assert_array_almost_equal(el, np.array([50.0, 100.0]))


def test_unexpected_loss_zero_at_extremes() -> None:
    """UL = 0 when PD = 0 or PD = 1 (no uncertainty)."""
    ead = np.array([1000.0])
    ul_pd0 = unexpected_loss_per_account(np.array([0.0]), 0.8, ead)
    ul_pd1 = unexpected_loss_per_account(np.array([1.0]), 0.8, ead)
    np.testing.assert_array_almost_equal(ul_pd0, np.array([0.0]))
    np.testing.assert_array_almost_equal(ul_pd1, np.array([0.0]))


def test_unexpected_loss_max_at_pd_half() -> None:
    """UL is maximized at PD = 0.5 (Bernoulli variance peak)."""
    ead = np.array([1000.0])
    ul_half = unexpected_loss_per_account(np.array([0.5]), 0.8, ead)
    ul_quarter = unexpected_loss_per_account(np.array([0.25]), 0.8, ead)
    assert ul_half[0] > ul_quarter[0]


def test_portfolio_summary_aggregates_correctly() -> None:
    """portfolio_summary sums EL, computes weighted average PD."""
    pd_arr = np.array([0.10, 0.20])
    lgd = 0.80
    ead = np.array([1000.0, 4000.0])  # heavier weight on the higher-PD account
    result = portfolio_summary(pd_arr, lgd, ead)
    assert isinstance(result, PortfolioLossResult)
    assert result.n_accounts == 2
    assert result.portfolio_ead == 5000.0
    expected_el = 0.10 * 0.80 * 1000 + 0.20 * 0.80 * 4000
    assert abs(result.portfolio_expected_loss - expected_el) < 1e-9
    # Weighted avg PD: (0.10 * 1000 + 0.20 * 4000) / 5000 = 0.18
    assert abs(result.weighted_avg_pd - 0.18) < 1e-9
    # Portfolio loss rate = EL / EAD
    assert abs(result.portfolio_loss_rate - expected_el / 5000) < 1e-9


def test_portfolio_unexpected_loss_independence_assumption() -> None:
    """Portfolio UL = sqrt(sum of per-account UL squares) under independence."""
    pd_arr = np.array([0.5, 0.5])
    lgd = 0.80
    ead = np.array([1000.0, 1000.0])
    per_account_ul = unexpected_loss_per_account(pd_arr, lgd, ead)
    expected_portfolio_ul = float(np.sqrt(np.sum(per_account_ul**2)))
    result = portfolio_summary(pd_arr, lgd, ead)
    assert abs(result.portfolio_unexpected_loss - expected_portfolio_ul) < 1e-9


def test_loss_components_frame_annotates_columns() -> None:
    """loss_components_frame adds ead/expected_loss/unexpected_loss columns."""
    df = pd.DataFrame(
        {
            'pd': [0.10, 0.05],
            'current_balance': [500.0, 1000.0],
            'credit_limit': [1000.0, 2000.0],
        }
    )
    out = loss_components_frame(
        df, 'pd', 'current_balance', 'credit_limit', lgd=0.80, draw_factor=0.60
    )
    assert 'ead' in out.columns
    assert 'expected_loss' in out.columns
    assert 'unexpected_loss' in out.columns
    assert len(out) == len(df)
    # Verify EAD for row 0: 500 + 0.60 * (1000 - 500) = 800
    assert abs(out['ead'].iloc[0] - 800.0) < 1e-9
    # Verify EL for row 0: 0.10 * 0.80 * 800 = 64
    assert abs(out['expected_loss'].iloc[0] - 64.0) < 1e-9


def test_loss_components_frame_missing_columns_raises() -> None:
    """Missing input columns surface a useful error."""
    df = pd.DataFrame({'pd': [0.10]})
    try:
        loss_components_frame(df, 'pd', 'balance_missing', 'limit_missing')
        raise AssertionError('should have raised')
    except ValueError as e:
        assert 'missing columns' in str(e)


def test_loss_rate_by_segment_segment_dimension() -> None:
    """loss_rate_by_segment produces one row per segment with EL/EAD/loss_rate."""
    df = pd.DataFrame(
        {
            'pd': [0.05, 0.05, 0.20, 0.20],
            'current_balance': [500.0, 500.0, 800.0, 800.0],
            'credit_limit': [1000.0, 1000.0, 1000.0, 1000.0],
            'segment_id': [0, 0, 5, 5],
        }
    )
    by_seg = loss_rate_by_segment(
        df,
        'pd',
        'current_balance',
        'credit_limit',
        segment_col='segment_id',
        lgd=0.80,
        draw_factor=0.60,
    )
    assert len(by_seg) == 2
    assert {
        'segment_id',
        'n_accounts',
        'portfolio_ead',
        'expected_loss',
        'loss_rate',
        'weighted_avg_pd',
    }.issubset(by_seg.columns)
    # High-PD segment should have higher loss_rate than low-PD segment
    seg0 = by_seg.loc[by_seg['segment_id'] == 0, 'loss_rate'].iloc[0]
    seg5 = by_seg.loc[by_seg['segment_id'] == 5, 'loss_rate'].iloc[0]
    assert seg5 > seg0


def test_default_constants_match_basel_retail_card() -> None:
    """Default LGD (80%) and draw factor (60%) are plausible Basel retail-card values."""
    assert DEFAULT_LGD_RETAIL_CARD == 0.80
    assert DEFAULT_DRAW_FACTOR == 0.60
