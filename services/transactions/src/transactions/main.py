"""Synthetic credit-transaction stream producer.

Two modes (selected via TXN_MODE env or --mode CLI flag):

  live      Emit events at the per-customer Poisson rate with current
            wall-clock timestamps. Default mode. Used for Day 1 smoke test
            and Day 5 drift-injection demo.

  backfill  Emit events spanning a historical [start, end] window using
            the heap-based merged Poisson process. Events arrive in
            chronological order. Used to load training data for Day 2.

See:
  - docs/dgp_design.md (DGP specification)
  - docs/01_problem_and_domain.md (business problem + cost-benefit)
  - docs/06_production_patterns.md (the three flavors of backfill)
  - deployments/dev/risingwave/00_source_transactions.sql (consumer schema)
"""

from __future__ import annotations

import signal
import sys
import time
from typing import Any

from loguru import logger
from quixstreams import Application

from transactions.backfill import backfill_events
from transactions.config import Mode, TransactionsSettings
from transactions.customer import generate_cohort
from transactions.generator import Event, TransactionGenerator
from transactions.state_checkpoint import load_states, save_states

_should_stop = False


def _on_signal(signum: int, _frame: Any) -> None:
    global _should_stop
    logger.info(f'Received signal {signum}; will stop after current event')
    _should_stop = True


_validate = False


def _emit(producer: Any, topic: Any, event: Event) -> None:
    if _validate:
        event.validate()
    msg = topic.serialize(key=event.customer_id, value=event.to_dict())
    producer.produce(topic=topic.name, value=msg.value, key=msg.key)


def _save_checkpoint(
    generator: TransactionGenerator,
    settings: TransactionsSettings,
    source_mode: str,
) -> None:
    """Best-effort checkpoint write; logs and swallows errors so failures
    here never crash the producer."""
    if not settings.state_enabled:
        return
    try:
        snapshots = generator.snapshot_states()
        save_states(
            snapshots,
            endpoint=settings.state_minio_endpoint,
            access_key=settings.state_minio_access_key,
            secret_key=settings.state_minio_secret_key,
            bucket=settings.state_minio_bucket,
            object_key=settings.state_object_key,
            seed=settings.seed,
            source_mode=source_mode,
        )
    except Exception as exc:
        logger.warning('state_checkpoint_save_failed mode={} exc={}', source_mode, exc)


def _run_live(
    producer: Any,
    topic: Any,
    generator: TransactionGenerator,
    settings: TransactionsSettings,
) -> int:
    """Emit events at real-time Poisson rates using the heap scheduler."""
    logger.info(
        f'Live mode: cohort_size={generator.cohort_size}, '
        f'heap-scheduled per-customer Poisson rates'
    )

    count = 0
    last_save_s = time.monotonic()
    while not _should_stop:
        # The heap knows when the next event should fire
        next_ms = generator.next_event_scheduled_time_ms()
        now_ms = int(time.time() * 1000)

        # Sleep until the next scheduled event (or emit immediately if behind)
        if next_ms > now_ms:
            sleep_s = (next_ms - now_ms) / 1000.0
            # Cap sleep to 1s to stay responsive to signals
            time.sleep(min(sleep_s, 1.0))
            if _should_stop:
                break
            continue

        # Emit with current wall-clock time
        ts_ms = int(time.time() * 1000)
        event = generator.next_event(ts_ms)
        _emit(producer, topic, event)
        count += 1

        if count % 100 == 0:
            logger.info(f'Live: emitted {count} events to topic={topic.name}')
        if settings.max_events is not None and count >= settings.max_events:
            logger.info(f'Reached max_events={settings.max_events}; stopping')
            break

        # Periodic checkpoint flush so a pod crash loses < state_save_interval_s
        # of accumulated state.
        if time.monotonic() - last_save_s >= settings.state_save_interval_s:
            _save_checkpoint(generator, settings, source_mode='live')
            last_save_s = time.monotonic()

    return count


def _run_backfill(
    producer: Any,
    topic: Any,
    generator: TransactionGenerator,
    settings: TransactionsSettings,
    cohort: list,
) -> int:
    """Emit historical events as fast as Kafka can accept."""
    if settings.start is None or settings.end is None:
        logger.error(
            'Backfill mode requires TXN_START and TXN_END (or --start/--end). '
            'Example: TXN_START=2026-03-01 TXN_END=2026-06-01'
        )
        sys.exit(1)

    cohort_rate = settings.events_per_sec_per_customer * generator.cohort_size
    count = 0
    for event in backfill_events(
        generator=generator,
        start=settings.start,
        end=settings.end,
        avg_events_per_sec=cohort_rate,
        seed=settings.seed,
        cohort=cohort,
    ):
        if _should_stop:
            logger.info(f'Stopping early after {count} events')
            break
        _emit(producer, topic, event)
        count += 1
        if count % 10_000 == 0:
            logger.info(f'Backfill: emitted {count} events')
        if settings.max_events is not None and count >= settings.max_events:
            logger.info(f'Reached max_events={settings.max_events}; stopping')
            break

    logger.info(f'Backfill complete: {count} total events emitted')
    return count


def main() -> None:
    global _validate

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    settings = TransactionsSettings()
    _validate = settings.validate
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    logger.info(
        f'Starting transactions producer: mode={settings.mode.value}, '
        f'cohort_size={settings.cohort_size}, seed={settings.seed}, '
        f'broker={settings.kafka_broker}, topic={settings.kafka_topic}'
    )

    cohort = generate_cohort(size=settings.cohort_size, seed=settings.seed)

    # Try to restore customer state from MinIO checkpoint. None if first run,
    # cohort/seed mismatch, or any error — generator falls back to baseline.
    restore_from = None
    if settings.state_enabled:
        try:
            restore_from = load_states(
                endpoint=settings.state_minio_endpoint,
                access_key=settings.state_minio_access_key,
                secret_key=settings.state_minio_secret_key,
                bucket=settings.state_minio_bucket,
                object_key=settings.state_object_key,
                expected_cohort_size=settings.cohort_size,
                expected_seed=settings.seed,
            )
        except Exception as exc:
            logger.warning('state_checkpoint_load_failed exc={}', exc)
            restore_from = None
    if restore_from is not None:
        logger.info('state_checkpoint_restored n_customers={}', len(restore_from))
    else:
        logger.info('state_checkpoint_cold_start using baseline_balance')

    generator = TransactionGenerator(
        cohort=cohort, seed=settings.seed, restore_from=restore_from
    )

    app = Application(broker_address=settings.kafka_broker)
    topic = app.topic(name=settings.kafka_topic, value_serializer='json')

    source_mode = 'backfill' if settings.mode == Mode.BACKFILL else 'live'
    try:
        with app.get_producer() as producer:
            if settings.mode == Mode.BACKFILL:
                count = _run_backfill(producer, topic, generator, settings, cohort)
            else:
                count = _run_live(producer, topic, generator, settings)
    finally:
        # Always checkpoint on exit (clean OR SIGTERM-driven), so backfill
        # leaves its accumulated state for live to pick up, and live
        # captures its latest state on graceful pod stop.
        _save_checkpoint(generator, settings, source_mode=source_mode)

    logger.info(f'Producer exited cleanly after {count} events')


if __name__ == '__main__':
    main()
