"""Outcome collector — runs the async consumer loop from collector.py."""

from __future__ import annotations

import asyncio
import os

from loguru import logger

from .collector import run


def main() -> None:
    broker = os.environ.get(
        'KAFKA_BROKER',
        'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092',
    )
    logger.info('outcome_collector_starting broker={}', broker)
    asyncio.run(run(broker))


if __name__ == '__main__':
    main()
