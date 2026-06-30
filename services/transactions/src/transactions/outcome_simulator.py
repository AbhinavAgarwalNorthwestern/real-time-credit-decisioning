"""Outcome simulator for offline A/B test of bandit policies.

Reads `decisions` Kafka topic. For each decision, looks up the customer's
ground-truth response params (true_p_accept_cli, true_delta_spend_if_accept,
true_p_default) and simulates the realized outcome:

    accepted ~ Bernoulli(effective_p_accept(customer, context))
    if accepted:
        defaulted ~ Bernoulli(effective_p_default(customer, context))
        realized_spend = true_delta_spend × Lognormal(0, 0.2)  # noise
        realized_profit = (
            realized_spend × NIM × discount_factor
            - defaulted × exposure × LGD
            - capital_cost(exposure)
        )
    else:
        realized_profit = 0.0  # no offer means no revenue and no loss

Emits to `outcomes` Kafka topic with decision_id, accepted, defaulted,
realized_profit, latency_to_outcome_ms (synthetic — 30 sec from decision
mimicking real-world feedback delay).

This is the offline component of the Phase L A/B test pipeline. Without
it, decisions flow through Kafka but no realized outcomes exist for the
OPE harness to evaluate champion vs challenger lift.

Constants from `docs/01_problem_and_domain.md`:
- NIM (net interest margin) = 0.12 annualized
- LGD (loss given default) = 0.80
- Discount factor = 0.95 (1-year horizon)
- Required capital = 0.08; capital cost rate = 0.08; → 0.64% of exposure
- Exposure on CLI accept = min(true_delta_spend × 12, 5000)

Per ADR 010 reference: this uses the SAME ground-truth params as the
training-time label simulator. So the model that wins on synthetic
outcomes here would also win in production — provided the DGP is honest.
"""

from __future__ import annotations

import json
import logging
import math
import os
import random
import signal
import time
from dataclasses import dataclass

# Standard MLflow/Kafka logging
logger = logging.getLogger(__name__)

# Economic constants (from docs/01_problem_and_domain.md §3)
NIM_ANNUAL: float = 0.12
LGD: float = 0.80
DISCOUNT_FACTOR: float = 0.95
CAPITAL_COST_RATE: float = 0.08 * 0.08  # required cap × cap cost rate
MAX_EXPOSURE_USD: float = 5_000.0
OUTCOME_DELAY_MEAN_S: float = 30.0  # synthetic feedback delay
OUTCOME_DELAY_STD_S: float = 5.0
SPEND_NOISE_SIGMA: float = 0.2  # lognormal noise around true_delta_spend


@dataclass(frozen=True, slots=True)
class GroundTruthParams:
    """Per-customer causal response parameters (latent — not observed by model)."""

    customer_id: str
    segment_id: int
    true_p_accept_cli: float
    true_delta_spend_if_accept: float
    true_p_default: float


@dataclass(frozen=True, slots=True)
class Decision:
    """Decision record consumed from Kafka `decisions` topic."""

    decision_id: str
    customer_id: str
    timestamp_ms: int
    action: str  # 'OFFER_CLI', 'NOTHING', 'FRAUD_CHECK'
    alias: str  # 'champion' or 'challenger'
    segment_id: int
    # Features observed at decision time — used to compute effective probs
    current_utilization: float
    velocity_24h: float | None
    paydown_rate_30d: float | None


@dataclass(frozen=True, slots=True)
class Outcome:
    """Realized outcome to emit to Kafka `outcomes` topic."""

    decision_id: str
    customer_id: str
    decision_timestamp_ms: int
    outcome_timestamp_ms: int
    accepted: bool
    defaulted: bool
    realized_spend_usd: float
    realized_profit_usd: float
    exposure_usd: float
    latency_to_outcome_ms: int

    def to_kafka_json(self) -> bytes:
        """Serialize for Kafka emission."""
        return json.dumps(
            {
                'decision_id': self.decision_id,
                'customer_id': self.customer_id,
                'decision_timestamp_ms': self.decision_timestamp_ms,
                'outcome_timestamp_ms': self.outcome_timestamp_ms,
                'accepted': self.accepted,
                'defaulted': self.defaulted,
                'realized_spend_usd': round(self.realized_spend_usd, 2),
                'realized_profit_usd': round(self.realized_profit_usd, 2),
                'exposure_usd': round(self.exposure_usd, 2),
                'latency_to_outcome_ms': self.latency_to_outcome_ms,
            },
            separators=(',', ':'),
        ).encode()


# -----------------------------------------------------------------------------
# Effective probability functions (context-dependent — match dgp_design.md §5)
# -----------------------------------------------------------------------------


def effective_p_accept(
    base_p_accept: float,
    current_utilization: float,
    velocity_24h: float | None,
) -> float:
    """Per dgp_design.md §5: utilization + velocity boost p_accept.

    Same function used at label-simulation time, so realized outcomes
    here align with what the training-time simulator would produce.
    """
    util_boost = 0.3 * (current_utilization - 0.5)
    velocity = velocity_24h if velocity_24h is not None else 0.0
    velocity_boost = 0.1 * min(velocity / 20.0, 1.0)
    p = base_p_accept + util_boost + velocity_boost
    return max(0.01, min(0.95, p))


def effective_p_default(
    base_p_default: float,
    current_utilization: float,
    paydown_rate_30d: float | None,
) -> float:
    """Per dgp_design.md §5: utilization + paydown adjust p_default."""
    util_risk = 0.2 * max(current_utilization - 0.6, 0.0)
    paydown = paydown_rate_30d if paydown_rate_30d is not None else 0.5
    paydown_risk = 0.1 * max(0.5 - paydown, 0.0)
    p = base_p_default + util_risk + paydown_risk
    return max(0.001, min(0.60, p))


# -----------------------------------------------------------------------------
# Outcome simulation
# -----------------------------------------------------------------------------


def simulate_outcome(
    decision: Decision,
    params: GroundTruthParams,
    rng: random.Random,
) -> Outcome:
    """Simulate the realized outcome for a single decision.

    NOTHING action → outcome has accepted=False, profit=0.
    FRAUD_CHECK → not modeled (separate accept/loss dynamics; outcome=0 for now).
    OFFER_CLI → full simulation per the equations in dgp_design.md §7.
    """
    decision_ts = decision.timestamp_ms
    delay_s = max(1.0, rng.normalvariate(OUTCOME_DELAY_MEAN_S, OUTCOME_DELAY_STD_S))
    outcome_ts = decision_ts + int(delay_s * 1000)
    latency_ms = int(delay_s * 1000)

    if decision.action != 'OFFER_CLI':
        return Outcome(
            decision_id=decision.decision_id,
            customer_id=decision.customer_id,
            decision_timestamp_ms=decision_ts,
            outcome_timestamp_ms=outcome_ts,
            accepted=False,
            defaulted=False,
            realized_spend_usd=0.0,
            realized_profit_usd=0.0,
            exposure_usd=0.0,
            latency_to_outcome_ms=latency_ms,
        )

    # OFFER_CLI: full simulation
    p_accept = effective_p_accept(
        params.true_p_accept_cli, decision.current_utilization, decision.velocity_24h
    )
    accepted = rng.random() < p_accept

    if not accepted:
        return Outcome(
            decision_id=decision.decision_id,
            customer_id=decision.customer_id,
            decision_timestamp_ms=decision_ts,
            outcome_timestamp_ms=outcome_ts,
            accepted=False,
            defaulted=False,
            realized_spend_usd=0.0,
            realized_profit_usd=0.0,
            exposure_usd=0.0,
            latency_to_outcome_ms=latency_ms,
        )

    # Accepted — sample realized spend (lognormal noise around mean)
    noise = math.exp(rng.normalvariate(0.0, SPEND_NOISE_SIGMA))
    realized_spend = params.true_delta_spend_if_accept * noise
    # Exposure on a 12-month CLI horizon, capped
    exposure = min(realized_spend * 12.0, MAX_EXPOSURE_USD)

    p_default = effective_p_default(
        params.true_p_default, decision.current_utilization, decision.paydown_rate_30d
    )
    defaulted = rng.random() < p_default

    nim_revenue = realized_spend * NIM_ANNUAL * DISCOUNT_FACTOR
    default_loss = exposure * LGD if defaulted else 0.0
    capital_cost = exposure * CAPITAL_COST_RATE
    realized_profit = nim_revenue - default_loss - capital_cost

    return Outcome(
        decision_id=decision.decision_id,
        customer_id=decision.customer_id,
        decision_timestamp_ms=decision_ts,
        outcome_timestamp_ms=outcome_ts,
        accepted=True,
        defaulted=defaulted,
        realized_spend_usd=realized_spend,
        realized_profit_usd=realized_profit,
        exposure_usd=exposure,
        latency_to_outcome_ms=latency_ms,
    )


# -----------------------------------------------------------------------------
# Kafka consumer/producer loop
# -----------------------------------------------------------------------------


async def run_simulator_loop(
    kafka_broker: str,
    decisions_topic: str = 'decisions',
    outcomes_topic: str = 'outcomes',
    consumer_group: str = 'outcome-simulator',
    customer_params_source: str | None = None,
    seed: int = 42,
) -> None:
    """Main consumer loop.

    Reads decisions, looks up ground-truth params, simulates outcome,
    emits to outcomes topic. Designed to run forever (long-running pod).

    `customer_params_source` is either a path to a parquet snapshot of
    customer ground-truth, or `None` to re-derive from generator using
    the master seed. For real-time operation, a parquet snapshot is
    preferred (lookup is O(1) hash table; no regeneration per event).
    """
    # Defer aiokafka import to keep module importable in environments
    # without kafka libs (tests, type checking).
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

    rng = random.Random(seed)
    params_lookup = _load_ground_truth(customer_params_source)
    logger.info(
        'simulator_start broker=%s decisions_topic=%s outcomes_topic=%s n_customers=%d',
        kafka_broker,
        decisions_topic,
        outcomes_topic,
        len(params_lookup),
    )

    consumer = AIOKafkaConsumer(
        decisions_topic,
        bootstrap_servers=kafka_broker,
        group_id=consumer_group,
        auto_offset_reset='earliest',
        value_deserializer=lambda v: json.loads(v.decode()),
    )
    producer = AIOKafkaProducer(bootstrap_servers=kafka_broker)
    await consumer.start()
    await producer.start()

    stop = False

    def _sigterm(*_: object) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    try:
        async for msg in consumer:
            if stop:
                break
            raw = msg.value
            decision = _parse_decision(raw)
            if decision is None:
                continue
            params = params_lookup.get(decision.customer_id)
            if params is None:
                logger.warning('skip_unknown_customer customer_id=%s', decision.customer_id)
                continue
            outcome = simulate_outcome(decision, params, rng)
            await producer.send_and_wait(
                outcomes_topic,
                value=outcome.to_kafka_json(),
                key=outcome.customer_id.encode(),
            )
    finally:
        await consumer.stop()
        await producer.stop()
        logger.info('simulator_stop')


def _parse_decision(raw: dict) -> Decision | None:
    """Best-effort decode of a Kafka decision record."""
    try:
        return Decision(
            decision_id=str(raw['decision_id']),
            customer_id=str(raw['customer_id']),
            timestamp_ms=int(raw['timestamp_ms']),
            action=str(raw['action']),
            alias=str(raw.get('alias', 'champion')),
            segment_id=int(raw.get('segment_id', 0)),
            current_utilization=float(raw.get('current_utilization', 0.5)),
            velocity_24h=raw.get('velocity_24h'),
            paydown_rate_30d=raw.get('paydown_rate_30d'),
        )
    except (KeyError, ValueError, TypeError) as e:
        logger.warning('decision_decode_error err=%s raw=%s', e, raw)
        return None


def _load_ground_truth(source: str | None) -> dict[str, GroundTruthParams]:
    """Load customer ground-truth params.

    If source is a path, expect parquet with columns
    [customer_id, segment_id, true_p_accept_cli, true_delta_spend_if_accept,
     true_p_default]. If None, re-derive from generator (slower; OK for tests).
    """
    if source is None:
        # Re-derive from generator (test path)
        from transactions.customer import generate_cohort
        from transactions.segments import SEGMENT_REGISTRY

        master_seed = int(os.environ.get('TXN_MASTER_SEED', '42'))
        cohort_size = int(os.environ.get('TXN_COHORT_SIZE', '1000'))
        customers = generate_cohort(
            size=cohort_size, master_seed=master_seed, segment_registry=SEGMENT_REGISTRY
        )
        return {
            c.customer_id: GroundTruthParams(
                customer_id=c.customer_id,
                segment_id=c.segment_id,
                true_p_accept_cli=c.true_p_accept_cli,
                true_delta_spend_if_accept=c.true_delta_spend_if_accept,
                true_p_default=c.true_p_default,
            )
            for c in customers
        }

    # Path-based: load parquet
    import pandas as pd

    df = pd.read_parquet(source)
    return {
        row['customer_id']: GroundTruthParams(
            customer_id=str(row['customer_id']),
            segment_id=int(row['segment_id']),
            true_p_accept_cli=float(row['true_p_accept_cli']),
            true_delta_spend_if_accept=float(row['true_delta_spend_if_accept']),
            true_p_default=float(row['true_p_default']),
        )
        for _, row in df.iterrows()
    }


def main() -> None:
    """Entry point. Called from `python -m transactions.outcome_simulator`."""
    import asyncio

    logging.basicConfig(
        level=os.environ.get('LOG_LEVEL', 'INFO'),
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
    )
    broker = os.environ.get(
        'KAFKA_BROKER', 'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092'
    )
    source = os.environ.get('TXN_GROUND_TRUTH_PARQUET')  # optional
    seed = int(os.environ.get('TXN_OUTCOME_SEED', '42'))
    asyncio.run(
        run_simulator_loop(
            kafka_broker=broker, customer_params_source=source, seed=seed
        )
    )


if __name__ == '__main__':
    main()
