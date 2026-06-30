"""decisioner — FastAPI application entrypoint.

Implements the collapsed request-path decomposition for credit-decisioning
(ADR 008, supersedes ADR 004). Per request:

  1. Feature lookup from RisingWave via asyncpg pool
  2. Per-segment ONNX inference (champion + challenger shadow)
  3. Canary routing — hash-based deterministic split
  4. Contextual bandit action selection
  5. Audit log enqueued to Kafka via aiokafka

Day 4 additions: challenger warm-up, canary fraction polling, watchdog loop.
Target: p99 < 50 ms.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from loguru import logger
from mlflow.tracking import MlflowClient
from prometheus_client import make_asgi_app

from decisioner.adverse_action import AdverseActionExplainer
from decisioner.audit import AuditLogProducer
from decisioner.champion_challenger import current_canary_fraction
from decisioner.config import settings
from decisioner.feature_lookup import make_pool
from decisioner.inference import ModelRegistry
from decisioner.metrics_collector import MetricsCollector
from decisioner.rate_limiter import RateLimiterMiddleware
from decisioner.rollback_watchdog import (
    WatchdogThresholds,
    execute_rollback,
    should_rollback,
)
from decisioner.routes.admin import router as admin_router
from decisioner.routes.decide import router as decide_router


async def _refresh_models_loop(app: FastAPI, interval_s: int) -> None:
    """Background task: poll MLflow registry for champion + challenger changes."""
    while True:
        try:
            await asyncio.sleep(interval_s)
            changed = await asyncio.to_thread(
                app.state.models.refresh_if_changed, 'champion'
            )
            if changed:
                logger.info('model_registry_refreshed', alias='champion')

            if settings.challenger_enabled:
                challenger_changed = await asyncio.to_thread(
                    app.state.models.refresh_if_changed, settings.challenger_alias
                )
                if challenger_changed:
                    logger.info(
                        'model_registry_refreshed', alias=settings.challenger_alias
                    )
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.error('model_refresh_failed', error=str(exc))


async def _canary_fraction_loop(app: FastAPI, interval_s: int) -> None:
    """Background task: poll MLflow for the canary_fraction tag on challenger."""
    mlflow_client: MlflowClient = app.state.mlflow_client
    while True:
        try:
            await asyncio.sleep(interval_s)
            fraction = await asyncio.to_thread(current_canary_fraction, mlflow_client)
            if fraction != app.state.canary_fraction:
                logger.info(
                    'canary_fraction_updated',
                    old=app.state.canary_fraction,
                    new=fraction,
                )
                app.state.canary_fraction = fraction
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning('canary_fraction_poll_failed', error=str(exc))


async def _watchdog_loop(app: FastAPI, interval_s: int) -> None:
    """Background task: monitor decision quality and auto-rollback challenger."""
    mlflow_client: MlflowClient = app.state.mlflow_client
    thresholds = WatchdogThresholds(
        p99_latency_max_degradation=settings.watchdog_p99_latency_max_degradation,
        profit_max_drop=settings.watchdog_profit_max_drop,
        error_rate_max=settings.watchdog_error_rate_max,
    )
    while True:
        try:
            await asyncio.sleep(interval_s)
            if app.state.canary_fraction <= 0.0:
                continue
            collector: MetricsCollector = app.state.metrics
            current = collector.current_window()
            baseline = collector.baseline_window()
            if current is None or baseline is None:
                continue
            do_rollback, reason = should_rollback(current, baseline, thresholds)
            if do_rollback:
                logger.warning('watchdog_triggered_rollback', reason=reason)
                await asyncio.to_thread(execute_rollback, mlflow_client)
                app.state.canary_fraction = 0.0
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            logger.error('watchdog_loop_error', error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info('decisioner_startup', host=settings.host, port=settings.port)

    app.state.db_pool = await make_pool(
        host=settings.rw_host,
        port=settings.rw_port,
        user=settings.rw_user,
        password=settings.rw_password,
        database=settings.rw_database,
        min_size=settings.rw_pool_min_size,
        max_size=settings.rw_pool_max_size,
        command_timeout=settings.rw_pool_acquire_timeout_s,
    )

    app.state.mlflow_client = MlflowClient(settings.mlflow_tracking_uri)

    app.state.models = ModelRegistry(
        tracking_uri=settings.mlflow_tracking_uri,
        intra_op_threads=settings.ort_intra_op_num_threads,
        inter_op_threads=settings.ort_inter_op_num_threads,
    )
    await asyncio.to_thread(app.state.models.warm_up, 'champion')

    # Attempt challenger warm-up (graceful no-op if alias doesn't exist)
    if settings.challenger_enabled:
        has_challenger = await asyncio.to_thread(
            app.state.models.has_alias, settings.challenger_alias
        )
        if has_challenger:
            await asyncio.to_thread(app.state.models.warm_up, settings.challenger_alias)
            logger.info('challenger_loaded', alias=settings.challenger_alias)
        else:
            logger.info('no_challenger_alias_found_at_startup')

    app.state.canary_fraction = 0.0
    app.state.metrics = MetricsCollector()
    app.state.adverse_action_explainer = AdverseActionExplainer(background_size=100)

    app.state.audit = AuditLogProducer(
        broker=settings.kafka_broker_address,
        topic=settings.kafka_audit_topic,
        linger_ms=settings.audit_producer_linger_ms,
    )
    await app.state.audit.start()

    app.state.refresh_task = asyncio.create_task(
        _refresh_models_loop(app, settings.model_refresh_interval_s)
    )
    app.state.canary_task = asyncio.create_task(
        _canary_fraction_loop(app, settings.canary_fraction_poll_interval_s)
    )
    if settings.watchdog_enabled:
        app.state.watchdog_task = asyncio.create_task(
            _watchdog_loop(app, settings.watchdog_poll_interval_s)
        )
    logger.info('decisioner_ready')

    try:
        yield
    finally:
        logger.info('decisioner_shutdown_start')
        app.state.refresh_task.cancel()
        app.state.canary_task.cancel()
        if settings.watchdog_enabled:
            app.state.watchdog_task.cancel()
        await app.state.audit.stop()
        await app.state.db_pool.close()
        logger.info('decisioner_shutdown_done')


app = FastAPI(
    title='Credit Decisioner',
    description='Real-time decision API per ADR 008.',
    version='0.1.0',
    lifespan=lifespan,
)

app.add_middleware(
    RateLimiterMiddleware,
    per_customer_rps=settings.rate_limit_per_customer_rps,
    global_rps=settings.rate_limit_global_rps,
)
app.include_router(decide_router)
app.include_router(admin_router)
app.mount('/metrics', make_asgi_app())


@app.get('/health')
async def health() -> dict[str, str]:
    return {'status': 'ok'}


def run() -> None:
    import uvicorn

    uvicorn.run(
        'decisioner.main:app',
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        log_level=settings.log_level.lower(),
        access_log=False,
    )


if __name__ == '__main__':
    run()
