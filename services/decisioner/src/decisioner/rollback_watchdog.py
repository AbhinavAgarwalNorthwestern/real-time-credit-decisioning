"""Day-4: Auto-rollback watchdog.

Reads metrics over a sliding window (currently from Prometheus; could be
swapped for the RW decision_log MV) and demotes a canary'd challenger
back to 0% if any of:

  - p99 decision latency degrades > 20%
  - mean_simulated_profit drops > 10% vs the prior 30-min window
  - error rate > 1%

This runs as a sidecar to the decisioner pod (or as a separate K8s
CronJob in production). It does NOT live in the request path — its
intervention is asynchronous and produces an MLflow audit trail.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from loguru import logger
from mlflow.tracking import MlflowClient


@dataclass(frozen=True, slots=True)
class WatchdogThresholds:
    p99_latency_max_degradation: float = 0.20
    profit_max_drop: float = 0.10
    error_rate_max: float = 0.01


@dataclass(frozen=True, slots=True)
class WindowMetrics:
    p99_latency_ms: float
    mean_profit: float
    error_rate: float


def should_rollback(
    current: WindowMetrics,
    baseline: WindowMetrics,
    thresholds: WatchdogThresholds,
) -> tuple[bool, str]:
    if current.error_rate > thresholds.error_rate_max:
        return (
            True,
            f'error_rate {current.error_rate:.3f} > {thresholds.error_rate_max}',
        )
    if baseline.p99_latency_ms > 0:
        delta_lat = (
            current.p99_latency_ms - baseline.p99_latency_ms
        ) / baseline.p99_latency_ms
        if delta_lat > thresholds.p99_latency_max_degradation:
            return (
                True,
                f'p99 latency +{delta_lat:.1%} > {thresholds.p99_latency_max_degradation:.1%}',
            )
    if baseline.mean_profit > 0:
        delta_prof = (baseline.mean_profit - current.mean_profit) / baseline.mean_profit
        if delta_prof > thresholds.profit_max_drop:
            return (
                True,
                f'mean_profit -{delta_prof:.1%} > {thresholds.profit_max_drop:.1%}',
            )
    return False, ''


def execute_rollback(
    client: MlflowClient, registered_model: str = 'uplift_per_segment'
) -> None:
    """Set the challenger canary_fraction tag to 0.0 and tag the rollback."""
    try:
        mv = client.get_model_version_by_alias(registered_model, 'challenger')
        client.set_model_version_tag(
            registered_model,
            str(mv.version),
            'canary_fraction',
            '0.0',
        )
        client.set_model_version_tag(
            registered_model,
            str(mv.version),
            'rolled_back',
            'true',
        )
        logger.warning(
            'challenger_rolled_back', model=registered_model, version=mv.version
        )
    except Exception as exc:  # noqa: BLE001
        logger.error('rollback_failed', error=str(exc))


async def watchdog_loop(
    client: MlflowClient,
    fetch_current: callable,  # type: ignore[type-arg]
    fetch_baseline: callable,  # type: ignore[type-arg]
    thresholds: WatchdogThresholds,
    poll_interval_s: int = 60,
) -> None:
    while True:
        try:
            current = await fetch_current()
            baseline = await fetch_baseline()
            do_rollback, reason = should_rollback(current, baseline, thresholds)
            if do_rollback:
                logger.warning('watchdog_triggered_rollback', reason=reason)
                execute_rollback(client)
            await asyncio.sleep(poll_interval_s)
        except asyncio.CancelledError:
            return
