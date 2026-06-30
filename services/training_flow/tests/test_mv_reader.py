"""Unit tests for the per-MV reader and the point-in-time join harness.

Specifically covers the non-windowed MV branch of `join_window_snapshots()`:
when a projection has no `window_end` column (e.g., the `customer_attributes`
MV that exposes constant-per-customer attributes), the merge must degrade to a
simple left-join on `customer_id` instead of `pd.merge_asof`.

In-memory DataFrame fixtures only; no RisingWave, no cluster.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from training_flow.mv_reader import (
    _PROJECTIONS,
    assert_point_in_time_correct,
    join_window_snapshots,
    list_managed_mvs,
)


def _windowed_base(cust: str, n: int, base_ts: datetime) -> pd.DataFrame:
    return pd.DataFrame(
        {
            'customer_id': [cust] * n,
            'window_end': [base_ts + timedelta(minutes=5 * i) for i in range(1, n + 1)],
            'velocity_5m': [i * 2 for i in range(1, n + 1)],
            'total_spend_5m': [i * 50.0 for i in range(1, n + 1)],
            'avg_spend_5m': [25.0] * n,
            'utilization': [0.3] * n,
        }
    )


def _customer_attributes(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_customer_attributes_is_managed() -> None:
    """The training pipeline must enumerate the new MV."""
    mvs = list_managed_mvs()
    assert 'customer_attributes' in mvs


def test_customer_attributes_projection_excludes_window_end() -> None:
    """Non-windowed MV: its projection must not name a window_end column."""
    proj = _PROJECTIONS['customer_attributes']
    assert 'window_end' not in proj
    for col in (
        'customer_id',
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert col in proj


def test_non_windowed_left_join_attaches_customer_attrs() -> None:
    """Each base row keeps all its columns + gains the 5 customer attributes."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = pd.concat(
        [_windowed_base('cust-A', 4, base_ts), _windowed_base('cust-B', 3, base_ts)],
        ignore_index=True,
    )
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            },
            {
                'customer_id': 'cust-B',
                'credit_score': 620,
                'annual_income': 45000.0,
                'account_tenure_months': 48,
                'n_products': 2,
                'prev_delinquency_count': 1,
            },
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    for col in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert col in joined.columns


def test_non_windowed_join_does_not_inflate_rows() -> None:
    """customer_attributes is one row per customer — left-join must not duplicate base rows."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = pd.concat(
        [_windowed_base('cust-A', 4, base_ts), _windowed_base('cust-B', 3, base_ts)],
        ignore_index=True,
    )
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            },
            {
                'customer_id': 'cust-B',
                'credit_score': 620,
                'annual_income': 45000.0,
                'account_tenure_months': 48,
                'n_products': 2,
                'prev_delinquency_count': 1,
            },
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    assert len(joined) == len(bf5)


def test_no_as_of_freshness_column_for_non_windowed() -> None:
    """Non-windowed MVs do not produce an `as_of_*` column (no freshness)."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = _windowed_base('cust-A', 3, base_ts)
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            }
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    assert 'as_of_customer_attributes' not in joined.columns


def test_orphan_customer_in_attributes_is_dropped() -> None:
    """Left-join semantics: a customer only present in customer_attributes (not the base)
    must not appear in the joined output. Otherwise we'd train on rows with no
    behavioral features."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = _windowed_base('cust-A', 3, base_ts)
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            },
            {
                'customer_id': 'cust-ORPHAN',
                'credit_score': 580,
                'annual_income': 30000.0,
                'account_tenure_months': 6,
                'n_products': 1,
                'prev_delinquency_count': 3,
            },
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    assert 'cust-ORPHAN' not in joined['customer_id'].values


def test_attributes_constant_within_customer() -> None:
    """All rows for a given customer must carry the SAME customer-attribute values."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = _windowed_base('cust-A', 5, base_ts)
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            }
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    distinct_scores = joined.loc[
        joined['customer_id'] == 'cust-A', 'credit_score'
    ].unique()
    assert len(distinct_scores) == 1
    assert distinct_scores[0] == 750


def test_assert_point_in_time_correct_unaffected_by_non_windowed_mv() -> None:
    """assert_point_in_time_correct() must remain happy after the non-windowed merge."""
    base_ts = datetime(2026, 6, 28, 12, 0)
    bf5 = _windowed_base('cust-A', 3, base_ts)
    ca = _customer_attributes(
        [
            {
                'customer_id': 'cust-A',
                'credit_score': 750,
                'annual_income': 85000.0,
                'account_tenure_months': 96,
                'n_products': 3,
                'prev_delinquency_count': 0,
            }
        ]
    )

    joined = join_window_snapshots(
        per_mv={'behavioral_features_5m': bf5, 'customer_attributes': ca},
        base_mv='behavioral_features_5m',
    )

    # Should not raise
    assert_point_in_time_correct(joined)
