"""Configuration for the transactions producer.

Env-driven via pydantic-settings. Every setting has the `TXN_` prefix
when read from environment. CLI flags can override env via the typer
parser in main.py (Day 2+).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic_settings import BaseSettings, SettingsConfigDict


class Mode(str, Enum):
    """Producer mode.

    LIVE: emit events at the configured per-customer rate with current
        wall-clock timestamps. Used in Day 1 smoke test and Day 5 drift
        injection demo.

    BACKFILL: emit events spanning a historical [start, end] window as
        fast as Kafka can accept. Used to load training data for Day 2
        models and historical replay scenarios.
    """

    LIVE = 'live'
    BACKFILL = 'backfill'


class TransactionsSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='TXN_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # Kafka destination
    # Default points at the kind cluster's external NodePort. Inside-cluster
    # deploys override this via env to the cluster-internal service URL.
    kafka_broker: str = 'localhost:31092'
    kafka_topic: str = 'transactions'

    # Generation parameters
    mode: Mode = Mode.LIVE
    cohort_size: int = 1000
    # Per-customer Poisson rate. With cohort_size=1000 and rate=0.05/sec/customer,
    # the cohort-wide rate is 50 events/sec — modest enough not to overwhelm
    # the dev cluster while still producing meaningful feature windows.
    events_per_sec_per_customer: float = 0.05
    seed: int = 42

    # Backfill-mode bounds (ignored in live mode)
    start: datetime | None = None
    end: datetime | None = None

    # Soft cap — useful for D2 demo to bound the run
    max_events: int | None = None

    # Debug-mode event validation (checks field bounds per event; off in prod)
    validate: bool = False

    # State checkpointing — persists customer balances/session state across
    # producer restarts so the cohort doesn't reset to baseline_balance on every
    # pod restart. See docs/scope_expansion_plan.md FAANG 2C for the full story.
    # When state_enabled=False, producer falls back to baseline_balance (legacy behavior).
    state_enabled: bool = True
    state_minio_endpoint: str = (
        'http://risingwave-minio.risingwave.svc.cluster.local:9000'
    )
    state_minio_access_key: str = ''
    state_minio_secret_key: str = ''
    state_minio_bucket: str = 'transactions-state'
    state_object_key: str = 'customer_states.json'
    # How often the live producer flushes state to MinIO. Backfill writes once at end.
    state_save_interval_s: int = 60

    # Logging
    log_level: str = 'INFO'
