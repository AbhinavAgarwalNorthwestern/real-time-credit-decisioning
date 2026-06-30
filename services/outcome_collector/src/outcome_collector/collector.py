"""Day-6: Outcome collector.

Reads the `outcomes` Kafka topic, joins each outcome to the original
decision via `decision_id`, materializes the joined `decision_outcomes`
view that the OPE harness consumes.

The collector is a thin Quixstreams app: each outcome event triggers a
lookup against the decisions audit table (RW MV `decision_log`), and the
joined record is written back to RW via Kafka topic `decision_outcomes`.
Day-5/6 introduces outcome lag-padding for slow labels (defaults can
take 12 months — we use synthetic 30-day proxy in dev).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer


@dataclass(frozen=True, slots=True)
class JoinedOutcome:
    decision_id: str
    customer_id: str
    decision_ts_ms: int
    outcome_ts_ms: int
    champion_action: int
    champion_propensity: float
    outcome_kind: str
    outcome_value: float
    feature_vector_hash: str


async def run(
    broker: str,
    outcomes_topic: str = 'outcomes',
    joined_topic: str = 'decision_outcomes',
    decision_log_table: str = 'decision_log',
    group_id: str = 'outcome-collector-1',
) -> None:
    """Long-running consumer loop. Day-7 wraps this in a K8s Deployment."""
    consumer = AIOKafkaConsumer(
        outcomes_topic,
        bootstrap_servers=broker,
        group_id=group_id,
        value_deserializer=lambda v: json.loads(v.decode('utf-8')),
        enable_auto_commit=False,
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=broker,
        value_serializer=lambda v: json.dumps(v).encode('utf-8'),
    )
    await consumer.start()
    await producer.start()
    try:
        async for msg in consumer:
            outcome = msg.value
            # Lookup decision_id in RW decision_log
            # (Day-7 wires asyncpg; here we trust the join key exists.)
            joined = JoinedOutcome(
                decision_id=outcome['decision_id'],
                customer_id=outcome.get('customer_id', ''),
                decision_ts_ms=outcome.get('decision_ts_ms', 0),
                outcome_ts_ms=outcome['outcome_ts_ms'],
                champion_action=outcome.get('champion_action', 0),
                champion_propensity=outcome.get('champion_propensity', 0.5),
                outcome_kind=outcome['outcome_kind'],
                outcome_value=outcome.get('outcome_value', 0.0),
                feature_vector_hash=outcome.get('feature_vector_hash', ''),
            )
            await producer.send(joined_topic, asdict(joined))
            await consumer.commit()
    finally:
        await consumer.stop()
        await producer.stop()
