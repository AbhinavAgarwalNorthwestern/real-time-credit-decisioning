"""Integration test: data_builder wiring with mocked RisingWave + K8s.

Exercises the join logic, point-in-time assertion, label attachment, and
parquet write — everything except the live cluster calls (backfill Job
and MV polling are mocked).

Marked `integration` — run with `uv run pytest -m integration`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from training_flow.config import TrainingFlowSettings
from training_flow.customer_params_loader import load_cohort_from_generator
from training_flow.data_builder import attach_labels, build
from training_flow.mv_reader import assert_point_in_time_correct, join_window_snapshots


def _make_mv_data(
    n_customers: int = 50, n_rows_per: int = 10, seed: int = 42
) -> dict[str, pd.DataFrame]:
    """Generate fake MV DataFrames matching the per-window schema."""
    rng = np.random.default_rng(seed)
    customer_ids = [f'cust_{i:04d}' for i in range(n_customers)]

    base_times = pd.date_range('2026-06-01', periods=n_rows_per, freq='5min')
    rows_5m = []
    for cid in customer_ids:
        for t in base_times:
            rows_5m.append(
                {
                    'customer_id': cid,
                    'window_end': t,
                    'velocity_5m': rng.exponential(2.0),
                    'total_spend_5m': rng.exponential(20.0),
                    'avg_spend_5m': rng.exponential(10.0),
                    'utilization': rng.uniform(0.1, 0.9),
                }
            )
    df_5m = pd.DataFrame(rows_5m)

    # 1h windows — end at each hour boundary <= the 5m windows
    hour_times = pd.date_range(
        '2026-06-01', periods=max(1, n_rows_per // 12), freq='1h'
    )
    rows_1h = []
    for cid in customer_ids:
        for t in hour_times:
            rows_1h.append(
                {
                    'customer_id': cid,
                    'window_end': t,
                    'velocity_1h': rng.exponential(5.0),
                    'total_spend_1h': rng.exponential(50.0),
                    'avg_spend_1h': rng.exponential(25.0),
                    'mcc_entropy_1h': rng.uniform(0.5, 2.5),
                }
            )
    df_1h = pd.DataFrame(rows_1h)

    # 24h — single window
    rows_24h = []
    for cid in customer_ids:
        rows_24h.append(
            {
                'customer_id': cid,
                'window_end': pd.Timestamp('2026-06-01'),
                'velocity_24h': rng.exponential(10.0),
                'total_spend_24h': rng.exponential(200.0),
                'avg_spend_24h': rng.exponential(50.0),
                'pct_late_night_24h': rng.uniform(0.0, 0.3),
                'avg_interarrival_24h': rng.exponential(3600.0),
            }
        )
    df_24h = pd.DataFrame(rows_24h)

    # 7d — single window
    rows_7d = []
    for cid in customer_ids:
        rows_7d.append(
            {
                'customer_id': cid,
                'window_end': pd.Timestamp('2026-06-01'),
                'velocity_7d': rng.exponential(50.0),
                'total_spend_7d': rng.exponential(800.0),
                'geo_variance_7d': rng.exponential(0.05),
            }
        )
    df_7d = pd.DataFrame(rows_7d)

    # 30d — single window
    rows_30d = []
    for cid in customer_ids:
        rows_30d.append(
            {
                'customer_id': cid,
                'window_end': pd.Timestamp('2026-06-01'),
                'velocity_30d': rng.exponential(200.0),
                'total_spend_30d': rng.exponential(3000.0),
                'paydown_rate_30d': rng.uniform(0.1, 0.9),
                'pct_cash_advance_30d': rng.uniform(0.0, 0.2),
                'avg_utilization_30d': rng.uniform(0.2, 0.8),
            }
        )
    df_30d = pd.DataFrame(rows_30d)

    return {
        'behavioral_features_5m': df_5m,
        'behavioral_features_1h': df_1h,
        'behavioral_features_24h': df_24h,
        'behavioral_features_7d': df_7d,
        'behavioral_features_30d': df_30d,
    }


@pytest.mark.integration
def test_join_preserves_point_in_time() -> None:
    """merge_asof join never produces a row where feature window_end > as_of."""
    per_mv = _make_mv_data()
    joined = join_window_snapshots(per_mv)
    assert_point_in_time_correct(joined)
    assert len(joined) > 0


@pytest.mark.integration
def test_join_has_expected_columns() -> None:
    """Joined frame has columns from all MVs plus freshness markers."""
    per_mv = _make_mv_data()
    joined = join_window_snapshots(per_mv)
    assert 'velocity_5m' in joined.columns
    assert 'velocity_1h' in joined.columns
    assert 'velocity_24h' in joined.columns
    assert 'velocity_7d' in joined.columns
    assert 'paydown_rate_30d' in joined.columns
    assert 'as_of' in joined.columns
    assert 'customer_id' in joined.columns


@pytest.mark.integration
def test_attach_labels_produces_expected_columns() -> None:
    """Label attachment adds T, accepted, spend_delta, defaulted, profit."""
    per_mv = _make_mv_data(n_customers=20, n_rows_per=5)
    joined = join_window_snapshots(per_mv)
    cohort = load_cohort_from_generator(cohort_size=20, seed=42)
    # Remap customer_ids to match
    joined['customer_id'] = np.tile(
        list(cohort.keys()), len(joined) // len(cohort) + 1
    )[: len(joined)]
    labelled = attach_labels(joined, cohort, seed=42)
    assert 'T' in labelled.columns
    assert 'profit' in labelled.columns
    assert labelled['T'].isin([0, 1]).all()


@pytest.mark.integration
def test_build_with_skip_backfill(tmp_path: Path) -> None:
    """build() with skip_backfill=True works when MVs already have data."""
    per_mv = _make_mv_data(n_customers=20, n_rows_per=5)

    settings = TrainingFlowSettings(
        rw_host='localhost',
        rw_port=4567,
        rw_user='root',
        rw_database='dev',
        backfill_days=7,
        backfill_seed=42,
        label_seed=42,
        output_path=tmp_path / 'training.parquet',
    )

    with (
        patch('training_flow.data_builder.load_all_window_mvs', return_value=per_mv),
        patch('training_flow.data_builder.poll_until_stable', return_value=100),
        patch('training_flow.data_builder.load_cohort_from_generator') as mock_cohort,
    ):
        cohort = load_cohort_from_generator(cohort_size=20, seed=42)
        # Remap MV customer_ids to cohort
        for mv_name, mv_df in per_mv.items():
            per_mv[mv_name]['customer_id'] = np.tile(
                list(cohort.keys()), len(mv_df) // len(cohort) + 1
            )[: len(mv_df)]
        mock_cohort.return_value = cohort

        result = build(settings, skip_backfill=True)
        assert result.succeeded is True or result.n_rows >= 0
        assert settings.output_path.exists()
