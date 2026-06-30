"""End-to-end Day-2 D2-1b training-data assembly.

Orchestrates:
  1. trigger_backfill — K8s Job runs the producer in TXN_MODE=backfill
  2. poll_until_stable — wait for every RW window MV to drain
  3. load_all_window_mvs + join_window_snapshots — assemble (customer,
     bucket, features) frame
  4. load_cohort_from_generator — recover each customer's ground-truth
     response params
  5. simulate_label — synthetic-RCT (T, Y) per ADR 010
  6. Write parquet

Designed for re-use by Day-5 `retraining_flow`: the same orchestrator
runs inside a Metaflow `@kubernetes` step; only the output destination
(local → MinIO) and the backfill window change.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from . import get_logger
from .backfill_trigger import (
    BackfillResult,
    request_window_days,
    trigger_backfill,
)
from .config import TrainingFlowSettings
from .customer_params_loader import load_cohort_from_generator
from .label_simulator import (
    CustomerParams,
    RowContext,
    SimulatedLabel,
    simulate_label,
)
from .mv_reader import (
    RWConnection,
    assert_point_in_time_correct,
    join_window_snapshots,
    list_managed_mvs,
    load_all_window_mvs,
    poll_until_stable,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BuildResult:
    output_path: Path
    n_rows: int
    n_customers: int
    backfill: BackfillResult


def _row_to_context(row: pd.Series) -> RowContext:
    """Adapter: pandas row → label_simulator.RowContext."""
    # Long-window MVs may produce NaN for new customers in early windows;
    # fall back to neutral values matching the cost-benefit table defaults.
    return RowContext(
        utilization=float(row.get('utilization', 0.5) or 0.5),
        velocity_24h=float(row.get('velocity_24h', 0.0) or 0.0),
        paydown_rate_30d=float(row.get('paydown_rate_30d', 0.5) or 0.5),
    )


def attach_labels(
    features: pd.DataFrame,
    cohort: dict[str, CustomerParams],
    seed: int,
) -> pd.DataFrame:
    """Add (T, accepted, spend_delta, defaulted, profit) columns to features.

    Deterministic for a given seed — caller controls reproducibility.
    Rows whose customer_id is not in the cohort are dropped (defensive:
    should not happen, but a stale MV with a dropped customer would).
    """
    rng = np.random.default_rng(seed=seed)
    treated: list[int] = []
    accepted: list[int] = []
    spend_delta: list[float] = []
    defaulted: list[int] = []
    profit: list[float] = []
    segment_ids: list[int] = []
    keep_idx: list[int] = []

    for idx, row in features.iterrows():
        cust = cohort.get(str(row['customer_id']))
        if cust is None:
            continue
        ctx = _row_to_context(row)
        lbl: SimulatedLabel = simulate_label(cust, ctx, rng)
        treated.append(lbl.treated)
        accepted.append(lbl.accepted)
        spend_delta.append(lbl.spend_delta)
        defaulted.append(lbl.defaulted)
        profit.append(lbl.profit)
        segment_ids.append(cust.segment_id)
        keep_idx.append(int(idx))  # type: ignore[arg-type]

    out = features.loc[keep_idx].copy()
    out['T'] = treated
    out['accepted'] = accepted
    out['spend_delta'] = spend_delta
    out['defaulted'] = defaulted
    out['profit'] = profit
    out['segment_id'] = segment_ids
    return out


def build(settings: TrainingFlowSettings, skip_backfill: bool = False) -> BuildResult:
    """End-to-end builder. Returns a BuildResult with paths and counts."""
    conn = RWConnection(
        host=settings.rw_host,
        port=settings.rw_port,
        user=settings.rw_user,
        database=settings.rw_database,
        password=settings.rw_password,
    )

    # 1. Backfill
    if skip_backfill:
        log.info(
            'build_step',
            step='backfill_trigger',
            action='SKIPPED',
            reason='--skip-backfill flag set, using existing MV data',
        )
        backfill_result = BackfillResult(
            job_name='skipped',
            succeeded=True,
            duration_s=0.0,
            pod_phase='Skipped',
        )
    else:
        log.info(
            'build_step',
            step='backfill_trigger',
            backfill_days=settings.backfill_days,
            seed=settings.backfill_seed,
            image=settings.transactions_image,
            namespace=settings.k8s_namespace,
        )
        req = request_window_days(
            days=settings.backfill_days, seed=settings.backfill_seed
        )
        backfill_result = trigger_backfill(
            req,
            namespace=settings.k8s_namespace,
            image=settings.transactions_image,
            timeout_s=settings.backfill_job_timeout_s,
        )
        if not backfill_result.succeeded:
            raise RuntimeError(f'backfill failed: {backfill_result}')
        log.info(
            'backfill_complete',
            job_name=backfill_result.job_name,
            duration_s=round(backfill_result.duration_s, 1),
        )

    # 2. Wait for MVs to drain (the base 5m is the most volatile signal)
    if not skip_backfill:
        log.info(
            'build_step',
            step='poll_until_stable',
            relation='behavioral_features_5m',
            poll_interval_s=settings.mv_poll_interval_s,
            stability_window_s=settings.mv_stability_window_s,
        )
        poll_until_stable(
            conn,
            relation='behavioral_features_5m',
            poll_interval_s=settings.mv_poll_interval_s,
            stability_window_s=settings.mv_stability_window_s,
        )
    else:
        log.info('build_step', step='poll_until_stable', action='SKIPPED')

    # 3. Query + join (point-in-time-correct merge_asof)
    mvs = list_managed_mvs()
    log.info('build_step', step='load_mvs', n_mvs=len(mvs), mvs=mvs)
    per_mv = load_all_window_mvs(conn)
    for mv_name, mv_df in per_mv.items():
        log.info('mv_loaded', mv=mv_name, rows=len(mv_df), columns=list(mv_df.columns))
    joined = join_window_snapshots(per_mv)
    assert_point_in_time_correct(joined)
    log.info(
        'joined_features_assembled',
        rows=len(joined),
        columns=len(joined.columns),
        customers=int(joined['customer_id'].nunique()),
        time_range_start=str(joined['as_of'].min()),
        time_range_end=str(joined['as_of'].max()),
    )

    # 4. Cohort (must match the seed the producer used)
    cohort_size = 1000
    log.info(
        'build_step',
        step='load_cohort',
        cohort_size=cohort_size,
        seed=settings.backfill_seed,
    )
    cohort = load_cohort_from_generator(
        cohort_size=cohort_size, seed=settings.backfill_seed
    )
    log.info('cohort_loaded', n_customers=len(cohort))

    # 5. Simulate labels
    log.info(
        'build_step',
        step='attach_labels',
        label_seed=settings.label_seed,
        n_input_rows=len(joined),
    )
    labelled = attach_labels(joined, cohort, seed=settings.label_seed)
    log.info(
        'labels_attached',
        rows=len(labelled),
        treated_pct=round(labelled['T'].mean() * 100, 1),
        mean_profit=round(float(labelled['profit'].mean()), 4),
    )

    # 6. Write parquet
    settings.output_path.parent.mkdir(parents=True, exist_ok=True)
    labelled.to_parquet(settings.output_path, index=False)
    log.info(
        'parquet_written',
        path=str(settings.output_path),
        rows=len(labelled),
        size_mb=round(settings.output_path.stat().st_size / 1024 / 1024, 2),
    )

    return BuildResult(
        output_path=settings.output_path,
        n_rows=len(labelled),
        n_customers=int(labelled['customer_id'].nunique()),
        backfill=backfill_result,
    )
