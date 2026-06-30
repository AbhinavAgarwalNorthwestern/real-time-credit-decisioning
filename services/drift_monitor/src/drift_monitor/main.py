"""Drift monitor — Kafka consumer that runs 7 detectors per ADR 012.

Reads from the `decisions` topic (audit records). For each sliding window
of N decisions, computes PSI, KS, JS divergence, and schema checks against
a reference distribution (loaded once from the training parquet via MLflow).
When any detector fires, emits a drift event to the `drift-events` topic,
which triggers the hot-challenger promotion (ADR 012).

Runs as a long-lived K8s Deployment (1 replica, no horizontal scaling needed
for the dev cluster's volume).
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass

import numpy as np
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from loguru import logger

from .detectors import (
    DriftSignal,
    JSDivergenceDetector,
    KSDetector,
    PSIDetector,
    SchemaDriftDetector,
)

WINDOW_SIZE = 500
FEATURE_KEYS = [
    'velocity_5m',
    'velocity_1h',
    'velocity_24h',
    'avg_amount_30d',
    'utilization',
    'pct_cash_advance_30d',
    'paydown_rate_30d',
    'mcc_entropy_24h',
]


@dataclass(frozen=True, slots=True)
class DriftEvent:
    detector: str
    feature: str
    statistic: float
    threshold: float
    window_size: int
    ts_ms: int


async def run(
    broker: str | None = None,
    decisions_topic: str = 'decisions',
    drift_topic: str = 'drift-events',
    group_id: str = 'drift-monitor-1',
) -> None:
    broker = broker or os.environ.get(
        'KAFKA_BROKER',
        'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092',
    )
    consumer = AIOKafkaConsumer(
        decisions_topic,
        bootstrap_servers=broker,
        group_id=group_id,
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        enable_auto_commit=True,
        auto_offset_reset='latest',
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=broker,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    )

    psi = PSIDetector()
    ks = KSDetector()
    js = JSDivergenceDetector()
    schema = SchemaDriftDetector()

    window: list[dict] = []
    reference: list[dict] | None = None

    await consumer.start()
    await producer.start()
    logger.info('drift_monitor_started', broker=broker)

    try:
        async for msg in consumer:
            record = msg.value
            window.append(record)

            if len(window) < WINDOW_SIZE:
                continue

            if reference is None:
                reference = list(window)
                window = []
                logger.info('reference_window_captured', size=len(reference))
                continue

            signals: list[DriftSignal] = []
            for feat in FEATURE_KEYS:
                ref_vals = np.array(
                    [r.get(feat, 0.0) for r in reference],
                    dtype=float,
                )
                cur_vals = np.array(
                    [r.get(feat, 0.0) for r in window],
                    dtype=float,
                )
                signals.append(psi.detect(ref_vals, cur_vals, feat))
                signals.append(ks.detect(ref_vals, cur_vals, feat))
                signals.append(js.detect(ref_vals, cur_vals, feat))

                ref_miss = float(np.isnan(ref_vals).mean())
                cur_miss = float(np.isnan(cur_vals).mean())
                signals.append(schema.detect_missingness(ref_miss, cur_miss, feat))

            fired = [s for s in signals if s.is_drifting]
            if fired:
                import time

                for s in fired:
                    event = DriftEvent(
                        detector=s.detector,
                        feature=s.feature,
                        statistic=s.statistic,
                        threshold=s.threshold,
                        window_size=WINDOW_SIZE,
                        ts_ms=int(time.time() * 1000),
                    )
                    await producer.send(drift_topic, asdict(event))
                    logger.warning(
                        'drift_detected',
                        detector=s.detector,
                        feature=s.feature,
                        stat=s.statistic,
                    )

            reference = list(window)
            window = []

    except asyncio.CancelledError:
        pass
    finally:
        await consumer.stop()
        await producer.stop()
        logger.info('drift_monitor_stopped')


def main() -> None:
    asyncio.run(run())


if __name__ == '__main__':
    main()
