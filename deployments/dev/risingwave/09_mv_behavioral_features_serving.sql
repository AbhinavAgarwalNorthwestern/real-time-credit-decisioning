-- ----------------------------------------------------------------------------
-- behavioral_features_serving — one row per customer, latest of every window
-- ----------------------------------------------------------------------------
-- The decisioner (Day 3) needs the LATEST feature values per customer at
-- decision time, across every window size. This MV produces a single row per
-- customer with the most recent value of every feature.
--
-- For Day-2 TRAINING, the data builder does NOT use this MV — it queries the
-- per-window MVs directly and joins them in pandas on (customer_id,
-- window_end_bucket) to get point-in-time-correct historical snapshots. The
-- serving MV always returns "now"; training needs "as-of t".
--
-- Implementation: SELECT DISTINCT ON (customer_id) ... ORDER BY ...
-- DESC per source MV, then FULL OUTER JOIN. The same pattern the existing
-- `behavioral_features_latest` uses for the 5m features.
--
-- Idempotent.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_serving AS
WITH latest_5m AS (
    SELECT DISTINCT ON (customer_id)
        customer_id,
        velocity_5m,
        total_spend_5m,
        avg_spend_5m,
        utilization,
        window_end AS as_of_5m
    FROM behavioral_features_5m
    ORDER BY customer_id, window_end DESC
),
latest_1h AS (
    SELECT DISTINCT ON (customer_id)
        customer_id,
        velocity_1h,
        total_spend_1h,
        avg_spend_1h,
        mcc_entropy_1h,
        window_end AS as_of_1h
    FROM behavioral_features_1h
    ORDER BY customer_id, window_end DESC
),
latest_24h AS (
    SELECT DISTINCT ON (customer_id)
        customer_id,
        velocity_24h,
        total_spend_24h,
        avg_spend_24h,
        pct_late_night_24h,
        avg_interarrival_24h,
        window_end AS as_of_24h
    FROM behavioral_features_24h
    ORDER BY customer_id, window_end DESC
),
latest_7d AS (
    SELECT DISTINCT ON (customer_id)
        customer_id,
        velocity_7d,
        total_spend_7d,
        geo_variance_7d,
        window_end AS as_of_7d
    FROM behavioral_features_7d
    ORDER BY customer_id, window_end DESC
),
latest_30d AS (
    SELECT DISTINCT ON (customer_id)
        customer_id,
        velocity_30d,
        total_spend_30d,
        paydown_rate_30d,
        pct_cash_advance_30d,
        avg_utilization_30d,
        window_end AS as_of_30d
    FROM behavioral_features_30d
    ORDER BY customer_id, window_end DESC
)
SELECT
    COALESCE(l5m.customer_id, l1h.customer_id, l24h.customer_id,
             l7d.customer_id, l30d.customer_id, ca.customer_id) AS customer_id,

    -- 5m window
    l5m.velocity_5m, l5m.total_spend_5m, l5m.avg_spend_5m,
    l5m.utilization, l5m.as_of_5m,

    -- 1h window
    l1h.velocity_1h, l1h.total_spend_1h, l1h.avg_spend_1h,
    l1h.mcc_entropy_1h, l1h.as_of_1h,

    -- 24h window
    l24h.velocity_24h, l24h.total_spend_24h, l24h.avg_spend_24h,
    l24h.pct_late_night_24h, l24h.avg_interarrival_24h, l24h.as_of_24h,

    -- 7d window
    l7d.velocity_7d, l7d.total_spend_7d, l7d.geo_variance_7d, l7d.as_of_7d,

    -- 30d window — the long-horizon risk signals
    l30d.velocity_30d, l30d.total_spend_30d,
    l30d.paydown_rate_30d, l30d.pct_cash_advance_30d,
    l30d.avg_utilization_30d, l30d.as_of_30d,

    -- Customer-level attributes (constant per customer, non-windowed)
    ca.credit_score, ca.annual_income, ca.account_tenure_months,
    ca.n_products, ca.prev_delinquency_count
FROM latest_5m l5m
FULL OUTER JOIN latest_1h  l1h  ON l5m.customer_id = l1h.customer_id
FULL OUTER JOIN latest_24h l24h ON COALESCE(l5m.customer_id, l1h.customer_id) = l24h.customer_id
FULL OUTER JOIN latest_7d  l7d  ON COALESCE(l5m.customer_id, l1h.customer_id, l24h.customer_id) = l7d.customer_id
FULL OUTER JOIN latest_30d l30d ON COALESCE(l5m.customer_id, l1h.customer_id, l24h.customer_id, l7d.customer_id) = l30d.customer_id
FULL OUTER JOIN customer_attributes ca ON COALESCE(l5m.customer_id, l1h.customer_id, l24h.customer_id, l7d.customer_id, l30d.customer_id) = ca.customer_id;
