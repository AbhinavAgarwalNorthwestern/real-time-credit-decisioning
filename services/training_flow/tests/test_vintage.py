"""Unit tests for vintage analysis (Phase B Item S2)."""

from __future__ import annotations

import pandas as pd
from training_flow.vintage import (
    VintageSummary,
    compute_vintage_curves,
    compute_vintage_summary,
    detect_vintage_drift,
    vintage_curves_to_dataframe,
)


def _make_cohort_df(
    cohort_origination: str,
    n_accounts: int,
    default_month_distribution: dict[int, int],
) -> pd.DataFrame:
    """Build a synthetic cohort DataFrame.

    default_month_distribution maps months_on_book → count of accounts that
    default at that MoB. The remainder (n_accounts - sum) are non-defaulters.
    """
    rows: list[dict] = []
    n_defaults = sum(default_month_distribution.values())
    if n_defaults > n_accounts:
        raise ValueError('default count exceeds cohort size')

    # Defaulters
    for mob, count in default_month_distribution.items():
        for _ in range(count):
            rows.append(
                {
                    'origination_date': cohort_origination,
                    'defaulted': 1,
                    'default_month': mob,
                }
            )
    # Non-defaulters
    for _ in range(n_accounts - n_defaults):
        rows.append(
            {
                'origination_date': cohort_origination,
                'defaulted': 0,
                'default_month': 0,
            }
        )
    return pd.DataFrame(rows)


def test_compute_vintage_curves_one_cohort_simple() -> None:
    """A single cohort with known defaults produces the expected cumulative curve."""
    df = _make_cohort_df(
        cohort_origination='2026-01-01',
        n_accounts=100,
        default_month_distribution={6: 5, 12: 10, 18: 3},
    )
    points = compute_vintage_curves(
        df,
        origination_col='origination_date',
        default_col='defaulted',
        default_month_col='default_month',
        max_months_on_book=24,
    )
    # 24 months × 1 cohort = 24 points
    assert len(points) == 24
    # At MoB 5: no defaults yet
    p5 = [p for p in points if p.months_on_book == 5][0]
    assert p5.n_defaults_cumulative == 0
    # At MoB 6: 5 defaults (5% rate)
    p6 = [p for p in points if p.months_on_book == 6][0]
    assert p6.n_defaults_cumulative == 5
    assert abs(p6.cumulative_default_rate - 0.05) < 1e-9
    # At MoB 18: cumulative 18 defaults (18% rate)
    p18 = [p for p in points if p.months_on_book == 18][0]
    assert p18.n_defaults_cumulative == 18
    # At MoB 24: final cumulative still 18 defaults
    p24 = [p for p in points if p.months_on_book == 24][0]
    assert p24.n_defaults_cumulative == 18


def test_cumulative_default_rate_monotonic() -> None:
    """Cumulative default rate must never decrease across months_on_book."""
    df = _make_cohort_df(
        cohort_origination='2026-01-01',
        n_accounts=200,
        default_month_distribution={3: 1, 6: 3, 12: 5, 18: 2, 22: 1},
    )
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=24
    )
    rates = [p.cumulative_default_rate for p in points]
    for i in range(len(rates) - 1):
        assert rates[i] <= rates[i + 1]


def test_two_cohorts_get_separate_curves() -> None:
    """Two cohorts produce two independent curves."""
    df1 = _make_cohort_df(
        cohort_origination='2026-01-01',
        n_accounts=100,
        default_month_distribution={6: 5},
    )
    df2 = _make_cohort_df(
        cohort_origination='2026-02-01',
        n_accounts=100,
        default_month_distribution={12: 20},
    )
    df = pd.concat([df1, df2], ignore_index=True)
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=18
    )
    cohorts = {p.origination_cohort for p in points}
    assert len(cohorts) == 2
    # 18 months × 2 cohorts = 36 points
    assert len(points) == 36


def test_vintage_curves_to_dataframe_round_trip() -> None:
    """vintage_curves_to_dataframe produces a DataFrame with expected columns."""
    df = _make_cohort_df('2026-01-01', 50, {6: 3, 12: 2})
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=12
    )
    out = vintage_curves_to_dataframe(points)
    assert {
        'origination_cohort',
        'months_on_book',
        'n_accounts',
        'n_defaults_cumulative',
        'cumulative_default_rate',
    }.issubset(out.columns)
    assert len(out) == 12


def test_vintage_summary_identifies_peak_correctly() -> None:
    """Peak month should be the FIRST month where cumulative rate is maxed."""
    df = _make_cohort_df(
        cohort_origination='2026-01-01',
        n_accounts=100,
        default_month_distribution={4: 1, 8: 4, 14: 2},
    )
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=24
    )
    summaries = compute_vintage_summary(points)
    assert len(summaries) == 1
    s = summaries[0]
    assert isinstance(s, VintageSummary)
    assert (
        s.peak_default_month == 14
    )  # last default month, where cumulative first reaches max
    assert abs(s.peak_cumulative_default_rate - 0.07) < 1e-9
    assert abs(s.final_cumulative_default_rate - 0.07) < 1e-9


def test_detect_vintage_drift_flags_worsening_cohort() -> None:
    """A newer cohort with materially higher default rate should be flagged."""
    df_old = _make_cohort_df(
        cohort_origination='2026-01-01',
        n_accounts=200,
        default_month_distribution={6: 2, 12: 2},  # 2% at MoB 12
    )
    df_new = _make_cohort_df(
        cohort_origination='2026-02-01',
        n_accounts=200,
        default_month_distribution={6: 10, 12: 10},  # 10% at MoB 12 — 5x worse
    )
    df = pd.concat([df_old, df_new], ignore_index=True)
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=24
    )
    flags = detect_vintage_drift(points, drift_threshold=0.50, comparison_mob=12)
    assert len(flags) == 1
    older, newer, rel = flags[0]
    assert older == '2026-01'
    assert newer == '2026-02'
    assert rel > 0.50


def test_detect_vintage_drift_no_flag_for_stable_cohorts() -> None:
    """Two cohorts with similar default rates should NOT be flagged."""
    df_a = _make_cohort_df(
        '2026-01-01',
        200,
        {6: 5, 12: 5},
    )
    df_b = _make_cohort_df(
        '2026-02-01',
        200,
        {6: 5, 12: 5},
    )
    df = pd.concat([df_a, df_b], ignore_index=True)
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=24
    )
    flags = detect_vintage_drift(points, drift_threshold=0.50, comparison_mob=12)
    assert flags == []


def test_missing_required_columns_raises() -> None:
    """Missing required columns should produce a helpful error."""
    df = pd.DataFrame({'origination_date': ['2026-01-01']})
    try:
        compute_vintage_curves(
            df,
            'origination_date',
            'defaulted',
            'default_month',
            max_months_on_book=12,
        )
        raise AssertionError('should have raised')
    except ValueError as e:
        assert 'missing columns' in str(e)


def test_cohort_freq_quarterly_groups() -> None:
    """cohort_freq='Q' should bucket monthly origination dates into quarters."""
    dfs = [
        _make_cohort_df('2026-01-15', 50, {6: 2}),  # Q1
        _make_cohort_df('2026-02-15', 50, {6: 2}),  # Q1 → same quarter
        _make_cohort_df('2026-04-15', 50, {6: 2}),  # Q2
    ]
    df = pd.concat(dfs, ignore_index=True)
    points = compute_vintage_curves(
        df,
        'origination_date',
        'defaulted',
        'default_month',
        max_months_on_book=12,
        cohort_freq='Q',
    )
    cohorts = {p.origination_cohort for p in points}
    assert len(cohorts) == 2  # Q1 (combining Jan+Feb) and Q2


def test_zero_default_cohort_handled() -> None:
    """A cohort with no defaults should produce all-zero curves without error."""
    df = _make_cohort_df('2026-01-01', 100, {})
    points = compute_vintage_curves(
        df, 'origination_date', 'defaulted', 'default_month', max_months_on_book=12
    )
    for p in points:
        assert p.n_defaults_cumulative == 0
        assert p.cumulative_default_rate == 0.0
