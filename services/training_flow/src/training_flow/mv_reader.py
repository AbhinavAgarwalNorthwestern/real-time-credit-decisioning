"""Read per-window RisingWave MVs into pandas for training.

Per ADR 002/009: RW is the feature store; the per-window MVs are the
single source of truth for features. Training queries them via Postgres
protocol (RW is wire-compatible).

Extensible: add a new MV by appending to `_PROJECTIONS`. The serving
decisioner reads from `behavioral_features_serving`; training reads
per-window so it can pick up point-in-time snapshots at any window_end.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import psycopg

from . import get_logger

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RWConnection:
    host: str
    port: int
    user: str
    database: str
    password: str = ''

    def dsn(self) -> str:
        if self.password:
            return (
                f'postgres://{self.user}:{self.password}'
                f'@{self.host}:{self.port}/{self.database}'
            )
        return f'postgres://{self.user}@{self.host}:{self.port}/{self.database}'


# Per-MV column projection. Source of truth for what training consumes.
# Add a new MV here when extending the feature set.
_PROJECTIONS: dict[str, str] = {
    'behavioral_features_5m': (
        'customer_id, window_end, velocity_5m, total_spend_5m, '
        'avg_spend_5m, utilization'
    ),
    'behavioral_features_1h': (
        'customer_id, window_end, velocity_1h, total_spend_1h, '
        'avg_spend_1h, mcc_entropy_1h'
    ),
    'behavioral_features_24h': (
        'customer_id, window_end, velocity_24h, total_spend_24h, '
        'avg_spend_24h, pct_late_night_24h, avg_interarrival_24h'
    ),
    'behavioral_features_7d': (
        'customer_id, window_end, velocity_7d, total_spend_7d, geo_variance_7d'
    ),
    'behavioral_features_30d': (
        'customer_id, window_end, velocity_30d, total_spend_30d, '
        'paydown_rate_30d, pct_cash_advance_30d, avg_utilization_30d'
    ),
    # Non-windowed MV — one row per customer with constant attributes. No
    # window_end column; join_window_snapshots() detects this and uses a
    # simple left-join on customer_id instead of merge_asof.
    'customer_attributes': (
        'customer_id, credit_score, annual_income, '
        'account_tenure_months, n_products, prev_delinquency_count'
    ),
}


def list_managed_mvs() -> list[str]:
    """Returns the list of MVs this module knows how to query.

    Day-5 retraining_flow uses this to enumerate the training feature
    surface without duplicating the projection list.
    """
    return list(_PROJECTIONS.keys())


def query_mv(conn: RWConnection, mv: str) -> pd.DataFrame:
    cols = _PROJECTIONS[mv]
    sql = f'SELECT {cols} FROM {mv}'
    log.info('mv_query_start', mv=mv)
    with psycopg.connect(conn.dsn(), autocommit=True) as cx:
        df = pd.read_sql_query(sql, cx)
    log.info('mv_query_done', mv=mv, rows=len(df))
    return df


def query_count(conn: RWConnection, relation: str) -> int:
    with psycopg.connect(conn.dsn(), autocommit=True) as cx:
        with cx.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM {relation}')
            row = cur.fetchone()
    if row is None:
        raise RuntimeError(f'count query returned no row for {relation}')
    return int(row[0])


def poll_until_stable(
    conn: RWConnection,
    relation: str,
    poll_interval_s: int = 30,
    stability_window_s: int = 120,
    timeout_s: int = 1800,
) -> int:
    """Block until `relation`'s row count is unchanged for stability_window_s.

    Returns the stable count. Raises TimeoutError on timeout. Used after
    triggering a backfill to know when RW has drained the new events.
    """
    start = time.monotonic()
    last_count = -1
    stable_since = start
    poll_count = 0

    log.info(
        'mv_poll_start',
        relation=relation,
        poll_interval_s=poll_interval_s,
        stability_window_s=stability_window_s,
        timeout_s=timeout_s,
    )

    while True:
        elapsed = time.monotonic() - start
        if elapsed > timeout_s:
            raise TimeoutError(
                f'{relation} did not stabilize within {timeout_s}s '
                f'(last count={last_count})'
            )

        count = query_count(conn, relation)
        poll_count += 1
        if count != last_count:
            log.info(
                'mv_growing',
                relation=relation,
                count=count,
                delta=count - max(last_count, 0),
                prev=last_count,
                poll=poll_count,
                elapsed_s=int(elapsed),
            )
            last_count = count
            stable_since = time.monotonic()
        else:
            stable_for = time.monotonic() - stable_since
            log.info(
                'mv_stable_check',
                relation=relation,
                count=count,
                stable_for_s=int(stable_for),
                need_s=stability_window_s,
                poll=poll_count,
                elapsed_s=int(elapsed),
            )
            if stable_for >= stability_window_s:
                log.info(
                    'mv_stabilized',
                    relation=relation,
                    count=count,
                    elapsed_s=int(elapsed),
                    polls=poll_count,
                )
                return count

        time.sleep(poll_interval_s)


def load_all_window_mvs(conn: RWConnection) -> dict[str, pd.DataFrame]:
    return {mv: query_mv(conn, mv) for mv in _PROJECTIONS}


def join_window_snapshots(
    per_mv: dict[str, pd.DataFrame],
    base_mv: str = 'behavioral_features_5m',
) -> pd.DataFrame:
    """Point-in-time-correct join: each row = features as-of one 5m bucket.

    **Point-in-time guarantee:** For a row whose `as_of` timestamp is T,
    every column comes from a window MV whose `window_end <= T`. The 5m
    MV gives features computed over [T-5min, T]; the 1h MV gives the most
    recent closed 1h window ending at-or-before T (so no event with
    event_time > T contributes); same for 24h / 7d / 30d.

    This eliminates training-time leakage: a model trained on these
    features sees exactly what the serving decisioner could have seen at
    decision time T.

    Implementation: `pd.merge_asof` with `direction='backward'`. For each
    base row, the merge picks the right row with the largest `window_end`
    not exceeding the base's `window_end`.

    NaN policy: long-window MVs (24h, 7d, 30d) won't have a closed window
    for early base rows. Those columns come back NaN — DOWNSTREAM CODE
    MUST EITHER (a) filter rows lacking closed long-window features, OR
    (b) impute with a documented neutral value. We do NOT silently
    forward-fill across customers.
    """
    if base_mv not in per_mv:
        raise KeyError(f'base_mv {base_mv!r} not in per_mv keys: {list(per_mv)}')

    base = per_mv[base_mv].copy()
    base['window_end'] = pd.to_datetime(base['window_end'])
    base = base.sort_values(['customer_id', 'window_end']).reset_index(drop=True)
    base = base.rename(columns={'window_end': 'as_of'})

    joined = base.copy()

    for mv, df in per_mv.items():
        if mv == base_mv:
            continue

        right = df.copy()

        # Non-windowed MVs (e.g., customer_attributes) have no window_end —
        # the values are constant per customer, so a plain left-join on
        # customer_id is point-in-time-safe (no temporal leakage possible).
        if 'window_end' not in right.columns:
            joined = joined.merge(right, on='customer_id', how='left')
            continue

        right['window_end'] = pd.to_datetime(right['window_end'])
        right = right.sort_values(['customer_id', 'window_end']).reset_index(drop=True)

        # Track the right-side window_end so callers can inspect feature
        # freshness and assert point-in-time correctness in tests.
        freshness_col = f'as_of_{mv.removeprefix("behavioral_features_")}'
        right = right.rename(columns={'window_end': freshness_col})

        # merge_asof requires globally sorted keys (not by group). We sort
        # by the time key first, then pass `by` for the customer grouping.
        right = right.sort_values(freshness_col).reset_index(drop=True)
        joined_sorted = joined.sort_values('as_of').reset_index(drop=True)

        joined_sorted = pd.merge_asof(
            joined_sorted,
            right,
            left_on='as_of',
            right_on=freshness_col,
            by='customer_id',
            direction='backward',
            allow_exact_matches=True,
        )
        joined = joined_sorted

    # Restore (customer_id, as_of) ordering for deterministic output
    joined = joined.sort_values(['customer_id', 'as_of']).reset_index(drop=True)
    return joined


def assert_point_in_time_correct(joined: pd.DataFrame) -> None:
    """Sanity assertion the data builder uses post-join.

    Every `as_of_*` freshness column must be <= the row's `as_of`. If any
    row violates, point-in-time correctness is broken and downstream
    training would leak future information.
    """
    freshness_cols = [c for c in joined.columns if c.startswith('as_of_')]
    for col in freshness_cols:
        mask = joined[col].notna() & (joined[col] > joined['as_of'])
        if mask.any():
            n_leaks = int(mask.sum())
            sample = joined.loc[mask, ['customer_id', 'as_of', col]].head(3)
            raise AssertionError(
                f'point-in-time leak detected in column {col}: '
                f'{n_leaks} rows have {col} > as_of. Sample:\n{sample}'
            )
