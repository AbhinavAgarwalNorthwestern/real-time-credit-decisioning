"""Env-driven config for the bias_monitor service."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class BiasMonitorSettings(BaseSettings):
    """Bias monitor configuration loaded from environment variables.

    Defaults are dev-cluster sensible; production should override via
    Kustomize overlay ConfigMaps + the External Secrets Operator (FAANG 1D).
    """

    model_config = SettingsConfigDict(env_prefix='BIAS_', case_sensitive=False)

    # Kafka topology
    kafka_broker: str = 'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092'
    decisions_topic: str = 'decisions'
    outcomes_topic: str = 'outcomes'
    drift_events_topic: str = 'drift-events'
    consumer_group: str = 'bias-monitor'

    # Fairness thresholds
    eighty_pct_rule_floor: float = 0.80
    equalized_odds_tolerance: float = 0.10
    predictive_parity_tolerance: float = 0.10

    # Operational
    window_size_decisions: int = 5000  # rolling window for fairness eval
    eval_interval_s: int = 60  # how often to compute + export metrics

    # Prometheus
    prom_http_port: int = 9090

    log_level: str = 'INFO'
