"""Configuration for the decisioner service. Env-driven (12-factor).

Settings load at module import and are immutable thereafter. Per-request
overrides are NOT supported by design — runtime config drift is a
production hazard.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class DecisionerSettings(BaseSettings):
    """Env-driven settings.

    All values overridable via env vars prefixed `DECISIONER_`.
    Example: `DECISIONER_PORT=8081`.
    """

    model_config = SettingsConfigDict(
        env_prefix='DECISIONER_',
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
    )

    # ------------------------------------------------------------------
    # HTTP server
    # ------------------------------------------------------------------
    host: str = '0.0.0.0'
    port: int = 8080
    log_level: str = 'INFO'

    # uvicorn workers per pod replica (Day 7 tunes this against measured p99)
    workers: int = 4

    # ------------------------------------------------------------------
    # RisingWave feature lookup (Day 1 wires this up)
    # ------------------------------------------------------------------
    rw_host: str = 'risingwave-frontend.risingwave.svc.cluster.local'
    rw_port: int = 4567
    rw_user: str = 'root'
    rw_password: str = ''
    rw_database: str = 'dev'

    # asyncpg connection pool
    rw_pool_min_size: int = 4
    rw_pool_max_size: int = 20
    rw_pool_acquire_timeout_s: float = 10.0

    # ------------------------------------------------------------------
    # MLflow + model loading (Day 2 wires this up)
    # ------------------------------------------------------------------
    mlflow_tracking_uri: str = 'http://mlflow-tracking.mlflow.svc.cluster.local'
    model_refresh_interval_s: int = 60
    """Polling interval for picking up new champion-alias model versions."""

    # onnxruntime tuning (Day 3)
    ort_intra_op_num_threads: int = 2
    ort_inter_op_num_threads: int = 1

    # ------------------------------------------------------------------
    # Audit log producer (Day 4 wires this up)
    # ------------------------------------------------------------------
    kafka_broker_address: str = (
        'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092'
    )
    kafka_audit_topic: str = 'decisions'
    audit_producer_linger_ms: int = 5
    """Producer batching window. Low for low audit-log latency; raise for higher throughput."""

    # ------------------------------------------------------------------
    # Champion-challenger (Day 4)
    # ------------------------------------------------------------------
    challenger_enabled: bool = True
    """When True, attempt to load and shadow-score challenger alias."""
    challenger_alias: str = 'challenger'
    canary_fraction_poll_interval_s: int = 30
    """How often to re-read the canary_fraction tag from MLflow."""

    # ------------------------------------------------------------------
    # Rollback watchdog (Day 4)
    # ------------------------------------------------------------------
    watchdog_enabled: bool = True
    watchdog_poll_interval_s: int = 60
    watchdog_p99_latency_max_degradation: float = 0.20
    watchdog_profit_max_drop: float = 0.10
    watchdog_error_rate_max: float = 0.01

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    rate_limit_per_customer_rps: int = 10
    rate_limit_global_rps: int = 1000

    # ------------------------------------------------------------------
    # SLO
    # ------------------------------------------------------------------
    decision_timeout_ms: int = 100
    """Hard per-request deadline. Beyond this we return a degraded response."""


settings = DecisionerSettings()
