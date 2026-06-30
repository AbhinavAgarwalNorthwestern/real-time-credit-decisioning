-- ----------------------------------------------------------------------------
-- events_enriched — per-event MV with LAG-derived signals
-- ----------------------------------------------------------------------------
-- Day 1 left feature work in a single 5-min tumbling MV. Day 2 adds features
-- that depend on event-to-event comparisons (paydown detection, interarrival
-- time) and per-event flags (cash advance, late night). Computing these inline
-- in every downstream window MV would duplicate logic; instead this MV emits
-- one enriched row per source event, and the windowed MVs aggregate over it.
--
-- Per ADR 009: feature definitions live in SQL. Per ADR 010: training reads
-- this MV via the per-window aggregates (`behavioral_features_{1h,24h,7d,30d}`).
--
-- Idempotent. Safe to re-apply.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS events_enriched AS
WITH lagged AS (
    SELECT
        event_id,
        customer_id,
        event_time,
        amount,
        mcc,
        merchant_id,
        geo_lat,
        geo_lon,
        is_card_present,
        current_balance,
        credit_limit,
        segment_id,
        -- Previous event's balance and event_time per customer, in chronological
        -- order. NULL on the first event for each customer.
        LAG(current_balance) OVER (
            PARTITION BY customer_id ORDER BY event_time
        ) AS prev_balance,
        LAG(event_time) OVER (
            PARTITION BY customer_id ORDER BY event_time
        ) AS prev_event_time
    FROM transactions
)
SELECT
    event_id,
    customer_id,
    event_time,
    amount,
    mcc,
    merchant_id,
    geo_lat,
    geo_lon,
    is_card_present,
    current_balance,
    credit_limit,
    segment_id,

    -- Paydown detection (see DGP design § 2.6 + generator.py:166-175):
    -- Paydowns are emitted silently by the producer — balance is reduced
    -- internally but no separate event is published. Non-paydown events
    -- always increase or hold current_balance (amount > 0, balance capped
    -- at credit_limit). Therefore a strict decrease between consecutive
    -- events for the same customer indicates a paydown fired between them.
    --
    -- Gap-guard (added with state-checkpointing rollout, v1.1.0): only
    -- flag paydowns when the previous event was within 1 hour. Across
    -- larger gaps, balance discontinuities can come from producer pod
    -- restarts (until checkpoint persistence lands fully) or from real
    -- multi-hour quiet periods we can't attribute. Suppressing across
    -- gaps avoids spurious paydown signals at the live/backfill boundary
    -- and after producer pod restarts.
    CASE
        WHEN prev_balance IS NOT NULL
         AND prev_balance > current_balance
         AND EXTRACT(EPOCH FROM event_time - prev_event_time) <= 3600.0
            THEN 1 ELSE 0
    END AS is_paydown,

    -- Cash advance flag (MCC 6010). Strongest single risk-tier signal in
    -- the DGP — high-risk segments draw 14-18% cash advances vs 1-2% for
    -- low-risk.
    CASE WHEN mcc = 6010 THEN 1 ELSE 0 END AS is_cash_advance,

    -- Late-night flag: 22:00-03:59 local. Maps to the circadian "high-risk"
    -- peak hours in DGP design § 3.1 (segments 4-5 have late evening peaks).
    -- We use UTC hour here for now; if/when we add per-customer timezones,
    -- this becomes a join. UTC is fine for the synthetic backfill (which
    -- emits in UTC).
    CASE
        WHEN EXTRACT(HOUR FROM event_time) >= 22
          OR EXTRACT(HOUR FROM event_time) < 4
            THEN 1 ELSE 0
    END AS is_late_night,

    -- Interarrival time in seconds. NULL on first event per customer.
    -- Captured per-event; the downstream 5m MV will AVG/MIN/MAX as needed.
    --
    -- Gap-guard cap (v1.1.0): bound at 3600s (1 hour). Without this, the
    -- transition event after a producer restart or live/backfill boundary
    -- could carry a multi-hour or multi-day interval that would distort
    -- avg_interarrival_24h aggregations downstream. Real customer
    -- inter-arrival is seconds-to-minutes; 3600s is "stale" but bounded.
    LEAST(
        EXTRACT(EPOCH FROM event_time - prev_event_time),
        3600.0
    )::DOUBLE PRECISION AS time_since_last_s
FROM lagged;
