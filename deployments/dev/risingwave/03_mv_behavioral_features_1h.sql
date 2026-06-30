-- ----------------------------------------------------------------------------
-- behavioral_features_1h — 1-hour tumbling features (incl. MCC entropy)
-- ----------------------------------------------------------------------------
-- 1-hour aggregates over events_enriched. Includes Shannon entropy over the
-- MCC distribution in the window — captures spending diversity, which the
-- DGP design (§ 2.5) flags as a risk-tier signal (high-risk = high entropy,
-- erratic spending).
--
-- Entropy computation is two-stage in SQL: per-(customer, window, mcc) count
-- first, then -SUM(p * ln(p)) where p = count / window_total.
--
-- Idempotent. Safe to re-apply.
-- ----------------------------------------------------------------------------

-- Stage 1: per-(customer, window, mcc) event counts. Intermediate MV; the
-- downstream entropy calc reads from this.
CREATE MATERIALIZED VIEW IF NOT EXISTS mcc_counts_1h AS
SELECT
    customer_id,
    window_start,
    window_end,
    mcc,
    COUNT(*)::BIGINT AS mcc_cnt
FROM TUMBLE(events_enriched, event_time, INTERVAL '1 hour')
GROUP BY customer_id, window_start, window_end, mcc;


-- Stage 2: combined per-customer-per-window aggregates.
-- Velocity / spend computed directly on the tumble.
-- Entropy computed from mcc_counts_1h via a self-join.
CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_1h AS
WITH base AS (
    SELECT
        customer_id,
        window_start,
        window_end,
        COUNT(*)::INT       AS velocity_1h,
        SUM(amount)         AS total_spend_1h,
        AVG(amount)         AS avg_spend_1h
    FROM TUMBLE(events_enriched, event_time, INTERVAL '1 hour')
    GROUP BY customer_id, window_start, window_end
),
window_totals AS (
    SELECT
        customer_id, window_start, window_end,
        SUM(mcc_cnt)::DOUBLE PRECISION AS total_in_window
    FROM mcc_counts_1h
    GROUP BY customer_id, window_start, window_end
),
entropy AS (
    -- H = -SUM(p_i * ln(p_i)) where p_i = mcc_cnt / total_in_window.
    -- Single-MCC windows produce ln(1) = 0, no issue.
    SELECT
        m.customer_id,
        m.window_start,
        m.window_end,
        -SUM(
            (m.mcc_cnt::DOUBLE PRECISION / w.total_in_window)
            * LN(m.mcc_cnt::DOUBLE PRECISION / w.total_in_window)
        ) AS mcc_entropy_1h
    FROM mcc_counts_1h m
    JOIN window_totals w
        ON m.customer_id = w.customer_id
       AND m.window_start = w.window_start
       AND m.window_end = w.window_end
    GROUP BY m.customer_id, m.window_start, m.window_end
)
SELECT
    b.customer_id,
    b.window_start,
    b.window_end,
    b.velocity_1h,
    b.total_spend_1h,
    b.avg_spend_1h,
    e.mcc_entropy_1h
FROM base b
LEFT JOIN entropy e
    ON  b.customer_id  = e.customer_id
    AND b.window_start = e.window_start
    AND b.window_end   = e.window_end;
