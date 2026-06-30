"""Day-3: feature lookup against the RisingWave serving MV.

Reads `behavioral_features_serving` — one row per customer with the
latest closed window of each horizon stitched together. Connection
pooling via asyncpg keeps lookup latency in the single-millisecond range.

Extensible: swap `_SERVING_MV` to `behavioral_features_serving_v2` when
the feature schema versions. Decisioner version is bumped alongside.
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass
from typing import Final

import asyncpg
import asyncpg.connection
from loguru import logger


class CircuitState(enum.Enum):
    CLOSED = 'closed'
    OPEN = 'open'
    HALF_OPEN = 'half_open'


class CircuitBreaker:
    """Simple circuit breaker for RisingWave connectivity."""

    def __init__(self, failure_threshold: int = 5, recovery_s: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._recovery_s = recovery_s
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._recovery_s:
                self._state = CircuitState.HALF_OPEN
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state is CircuitState.OPEN

    def allow_request(self) -> bool:
        s = self.state
        return s in (CircuitState.CLOSED, CircuitState.HALF_OPEN)

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._state is CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning('circuit_breaker_reopened')
        elif self._consecutive_failures >= self._failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.warning(
                'circuit_breaker_opened failures={}', self._consecutive_failures
            )


_breaker = CircuitBreaker()


def get_circuit_breaker() -> CircuitBreaker:
    return _breaker


async def _noop_reset(
    self: asyncpg.Connection, *, timeout: float | None = None
) -> None:
    return


asyncpg.connection.Connection.reset = _noop_reset

_SERVING_MV: Final[str] = 'behavioral_features_serving'

# Keep aligned with deployments/dev/risingwave/09_mv_behavioral_features_serving.sql.
# Order is the canonical model-input order; do NOT reorder without bumping
# the model's payload_version and the manifest in mlflow_log.feature_schema.
_FEATURE_COLS: Final[tuple[str, ...]] = (
    'velocity_5m',
    'total_spend_5m',
    'avg_spend_5m',
    'utilization',
    'velocity_1h',
    'total_spend_1h',
    'avg_spend_1h',
    'mcc_entropy_1h',
    'velocity_24h',
    'total_spend_24h',
    'avg_spend_24h',
    'pct_late_night_24h',
    'avg_interarrival_24h',
    'velocity_7d',
    'total_spend_7d',
    'geo_variance_7d',
    'velocity_30d',
    'total_spend_30d',
    'paydown_rate_30d',
    'pct_cash_advance_30d',
    'avg_utilization_30d',
    # Customer-level attributes (joined from customer_attributes MV in 09_).
    'credit_score',
    'annual_income',
    'account_tenure_months',
    'n_products',
    'prev_delinquency_count',
)


@dataclass(frozen=True, slots=True)
class CustomerFeatures:
    customer_id: str
    values: tuple[float, ...]  # ordered per _FEATURE_COLS
    segment_id: int | None  # populated when known (cohort metadata)


def feature_column_order() -> tuple[str, ...]:
    """Public — model inference uses this to assemble the input vector."""
    return _FEATURE_COLS


async def fetch_one(
    pool: asyncpg.Pool,
    customer_id: str,
    breaker: CircuitBreaker | None = None,
) -> CustomerFeatures | None:
    """Returns CustomerFeatures or None if customer not in serving MV."""
    cb = breaker or _breaker
    if not cb.allow_request():
        logger.warning('circuit_open_skipping_rw customer_id={}', customer_id)
        return None
    cols_csv = ', '.join(_FEATURE_COLS)
    sql = f'SELECT {cols_csv} FROM {_SERVING_MV} WHERE customer_id = $1'
    try:
        async with pool.acquire() as cx:
            row = await cx.fetchrow(sql, customer_id)
    except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
        cb.record_failure()
        logger.error('rw_fetch_failed customer_id={} error={}', customer_id, exc)
        return None
    cb.record_success()
    if row is None:
        return None
    values = tuple((row[c] if row[c] is not None else 0.0) for c in _FEATURE_COLS)
    return CustomerFeatures(
        customer_id=customer_id,
        values=values,
        segment_id=None,
    )


async def make_pool(
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    min_size: int = 4,
    max_size: int = 20,
    command_timeout: float = 2.0,
) -> asyncpg.Pool:
    logger.info(
        'rw_pool_creating', host=host, port=port, min_size=min_size, max_size=max_size
    )

    async def _init_conn(conn: asyncpg.Connection) -> None:
        await conn.execute('SET RW_IMPLICIT_FLUSH TO true;')

    pool = await asyncpg.create_pool(
        host=host,
        port=port,
        user=user,
        password=password or None,
        database=database,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        statement_cache_size=0,
        init=_init_conn,
    )
    logger.info('rw_pool_ready')
    assert pool is not None
    return pool
