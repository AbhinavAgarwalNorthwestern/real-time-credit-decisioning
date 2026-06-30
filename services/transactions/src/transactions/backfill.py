"""Backfill mode for the transactions producer.

Emits historical events spanning a [start, end] window using the
heap-based merged Poisson process. Events are emitted in chronological
order by consuming from the generator's internal min-heap until the
scheduled time exceeds the end boundary.

Used for:
  - Day 2: load 30–90 days of history into RisingWave for model training
  - Day 5: replay scenarios when validating retraining flows
  - Day 6: bulk-load synthetic decision+outcome history for OPE

See docs/06_production_patterns.md for the three flavors of backfill.
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from datetime import datetime

import numpy as np
from loguru import logger

from transactions.customer import Customer
from transactions.generator import Event, TransactionGenerator


def _init_generator_at(
    cohort: list[Customer],
    start: datetime,
    seed: int,
) -> TransactionGenerator:
    """Create a generator with the heap initialized at `start` time.

    The default TransactionGenerator uses datetime.now() for initial
    scheduling. For backfill we need to schedule from a historical start.
    """
    gen = TransactionGenerator(cohort=cohort, seed=seed)

    # Re-initialize the heap with start-relative scheduling
    rng = np.random.default_rng(seed + 1)
    start_ms = int(start.timestamp() * 1000)
    gen._heap = []
    for i, customer in enumerate(cohort):
        # Jitter start: spread initial events across [0, 1/rate] seconds
        if customer.txn_rate > 0:
            jitter_s = float(rng.exponential(1.0 / customer.txn_rate))
        else:
            jitter_s = 60.0
        first_ms = start_ms + int(jitter_s * 1000)
        heapq.heappush(gen._heap, (first_ms, i))

    return gen


def backfill_events(
    generator: TransactionGenerator,
    start: datetime,
    end: datetime,
    avg_events_per_sec: float,
    seed: int = 42,
    cohort: list[Customer] | None = None,
) -> Iterator[Event]:
    """Yield events with historical timestamps from start to end.

    Uses the heap-based merged Poisson process: each call to
    generator.next_event() produces the chronologically next event
    across all customers. Stops when the scheduled time exceeds `end`.

    Args:
        generator: ignored if cohort is provided (legacy compat); otherwise
            used directly with its current heap state
        start: earliest event_time to emit
        end: latest event_time to emit
        avg_events_per_sec: used for progress estimation only
        seed: RNG seed for heap initialization
        cohort: if provided, creates a fresh generator initialized at start

    Yields:
        Event objects in chronological order
    """
    if start >= end:
        raise ValueError(f'start ({start}) must be before end ({end})')

    if cohort is not None:
        gen = _init_generator_at(cohort, start, seed)
    else:
        gen = generator

    end_ms = int(end.timestamp() * 1000)
    total_seconds = (end - start).total_seconds()
    estimated_events = int(total_seconds * avg_events_per_sec)

    logger.info(
        f'Backfill: estimated ~{estimated_events:,} events from '
        f'{start.isoformat()} to {end.isoformat()} ({total_seconds / 86400:.1f} days)'
    )

    count = 0
    while gen.next_event_scheduled_time_ms() <= end_ms:
        event = gen.next_event()
        count += 1
        yield event

        if count % 50_000 == 0:
            pct = count / max(estimated_events, 1) * 100
            logger.info(f'Backfill progress: {count:,} events ({pct:.1f}%)')

    logger.info(f'Backfill complete: {count:,} total events emitted')
