-- ----------------------------------------------------------------------------
-- transactions source — Kafka topic → RisingWave
-- ----------------------------------------------------------------------------
-- RisingWave consumes events directly from the `transactions` Kafka topic
-- produced by `services/transactions/`. Schema mirrors the producer's
-- Transaction dataclass.
--
-- Per ADR 009: feature computation happens in RisingWave SQL (the materialized
-- views over this source), not in a Quixstreams Python service.
--
-- This file is idempotent: `CREATE SOURCE IF NOT EXISTS` lets re-apply succeed.
-- ----------------------------------------------------------------------------

CREATE SOURCE IF NOT EXISTS transactions (
    event_id         VARCHAR,
    customer_id      VARCHAR,
    timestamp_ms     BIGINT,
    amount           DOUBLE PRECISION,
    mcc              INT,
    merchant_id      VARCHAR,
    geo_lat          DOUBLE PRECISION,
    geo_lon          DOUBLE PRECISION,
    is_card_present  BOOLEAN,
    current_balance  DOUBLE PRECISION,
    credit_limit     DOUBLE PRECISION,
    segment_id       INT,
    -- Customer-level attributes: constant per customer, denormalized onto
    -- every event so the customer_attributes MV (08) can MAX() per customer.
    credit_score             INT,
    annual_income            DOUBLE PRECISION,
    account_tenure_months    INT,
    n_products               INT,
    prev_delinquency_count   INT,
    payload_version  INT,
    -- Generated column: convert timestamp_ms to TIMESTAMP for event-time
    -- semantics. RisingWave requires generated columns to declare their type
    -- explicitly (the leading `TIMESTAMP` here is required — without it the
    -- SQL parser fails with "invalid data_type").
    event_time       TIMESTAMPTZ AS to_timestamp(timestamp_ms / 1000),
    -- Watermark: tolerate events arriving up to 10 seconds after their
    -- event_time. RisingWave uses this to decide when a tumbling window
    -- can safely close.
    WATERMARK FOR event_time AS event_time - INTERVAL '10 seconds'
)
-- scan.startup.mode = 'latest' rather than 'earliest':
-- this skips snapshot backfill of accumulated Kafka history when the source
-- (and its dependent MVs) are first created. This avoids the Hummock
-- "decreasing now" bug that triggers when LAG()-based MVs (events_enriched)
-- replay historical events. New events flow forward-only after the producer
-- is restarted; historical training data is reproduced via the backfill_trigger
-- K8s Job (see services/training_flow/src/training_flow/backfill_trigger.py).
WITH (
    connector = 'kafka',
    topic = 'transactions',
    properties.bootstrap.server = 'kafka-e11b-kafka-bootstrap.kafka.svc.cluster.local:9092',
    scan.startup.mode = 'latest'
)
FORMAT PLAIN ENCODE JSON;
