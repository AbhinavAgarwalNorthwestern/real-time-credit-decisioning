"""Env-driven settings for the Day-2 training pipeline.

Every setting reads from environment with the `TF_` prefix.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class TrainingFlowSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix='TF_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # RisingWave (feature store) connection
    rw_host: str = 'localhost'
    rw_port: int = 4567
    rw_user: str = 'root'
    rw_database: str = 'dev'
    rw_password: str = ''

    # Backfill configuration
    backfill_days: int = 7
    backfill_seed: int = 42

    # K8s job parameters
    k8s_namespace: str = 'real-time-ml'
    transactions_image: str = 'localhost:5000/transactions:dev'
    backfill_job_timeout_s: int = 3600

    # MV polling — how long to wait for RW to settle after backfill
    mv_poll_interval_s: int = 30
    mv_stability_window_s: int = 120

    # Output
    output_path: Path = Path('data/training.parquet')

    # Label simulation (per ADR 010)
    label_seed: int = 42
    # Bernoulli(0.5) RCT — codified in the simulator; here as documentation.
    treatment_assignment_p: float = 0.5

    # Logging
    log_level: str = 'INFO'
