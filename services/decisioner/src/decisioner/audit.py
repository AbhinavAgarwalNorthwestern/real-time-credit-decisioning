"""Day-3/4: Async decision audit log to Kafka.

Per docs/02_data_and_features.md "Decision audit schema". One row per
/decide call → Kafka topic `decisions` → RW MV `decision_log` → Day-6 OPE.

aiokafka producer with linger_ms=5 batches without breaking the SLO. On
producer shutdown we flush — never drop pending audits at SIGTERM.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass

from aiokafka import AIOKafkaProducer
from loguru import logger


@dataclass(frozen=True, slots=True)
class AuditRecord:
    decision_id: str
    customer_id: str
    decision_ts_ms: int
    segment: int
    champion_model_uri: str
    challenger_model_uri: str | None
    champion_action: int  # Action enum integer
    champion_propensity: float
    challenger_action: int | None
    challenger_propensity: float | None
    acted_on_alias: str  # 'champion' or 'challenger'
    feature_vector_hash: str  # blake2b hex; reproducibility key
    shap_delta_baseline: dict[str, float] | None  # Day-6 fills in
    regulatory_flags: dict[str, bool]


class AuditLogProducer:
    def __init__(self, broker: str, topic: str, linger_ms: int = 5) -> None:
        self._broker = broker
        self._topic = topic
        self._linger_ms = linger_ms
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=self._broker,
            linger_ms=self._linger_ms,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            key_serializer=lambda k: k.encode('utf-8') if k else None,
        )
        await self._producer.start()
        logger.info('audit_producer_started', broker=self._broker, topic=self._topic)

    async def stop(self) -> None:
        if self._producer is not None:
            await self._producer.flush()
            await self._producer.stop()
            logger.info('audit_producer_stopped')

    async def emit(self, record: AuditRecord) -> None:
        if self._producer is None:
            raise RuntimeError('AuditLogProducer not started')
        await self._producer.send(
            self._topic,
            value=asdict(record),
            key=record.customer_id,
        )


def new_decision_id() -> str:
    return uuid.uuid4().hex


def now_ms() -> int:
    return int(time.time() * 1000)
