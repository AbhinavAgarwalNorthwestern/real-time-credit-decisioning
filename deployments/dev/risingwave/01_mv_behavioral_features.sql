-- ----------------------------------------------------------------------------
-- behavioral_features — minimum-viable Day 1 implementation
-- ----------------------------------------------------------------------------
-- Computes per-customer behavioral features over a 5-minute tumbling window
-- on top of the `transactions` source.
--
-- Day 1 scope (MVP):
--   - velocity_5m     = COUNT(*) per customer per 5-min window
--   - total_spend_5m  = SUM(amount) per customer per 5-min window
--   - avg_spend_5m    = AVG(amount) per customer per 5-min window
--   - utilization     = current_balance / credit_limit (latest per window)
--
-- Day 2+ will add:
--   - mcc_entropy_1h  (Shannon entropy over MCC distribution in 1h window)
--   - geo_anomaly_z   (z-score of current geo vs customer's rolling mean/std)
--   - time_since_last (LAG-based interarrival)
--   - macro_sentiment_1h  (JOIN with news_sentiment MV)
--   - additional window sizes (1h, 24h)
--
-- The decisioner (services/decisioner) reads from this MV via the asyncpg
-- Postgres protocol — see ADR 004 / ADR 008 for the request-path design.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_5m AS
SELECT
    customer_id,
    window_start,
    window_end,

    -- Velocity: number of transactions in this 5-minute window.
    COUNT(*)::INT                                  AS velocity_5m,

    -- Spend: total + average over the window.
    SUM(amount)                                    AS total_spend_5m,
    AVG(amount)                                    AS avg_spend_5m,

    -- Utilization: the latest credit utilization observed in the window.
    -- We use MAX(current_balance) / MAX(credit_limit) — these are both
    -- point-in-time per event, and since they're customer-stable across
    -- a window MAX captures the latest observed value cleanly.
    MAX(current_balance) / NULLIF(MAX(credit_limit), 0)  AS utilization
FROM TUMBLE(
    transactions,
    event_time,
    INTERVAL '5 minutes'
)
GROUP BY customer_id, window_start, window_end;


-- ----------------------------------------------------------------------------
-- Latest-window snapshot — used by the decisioner for serving.
-- ----------------------------------------------------------------------------
-- The decisioner needs the *most recent* window per customer at decision time,
-- not the whole history. This MV gives it that single row per customer.
-- It refreshes every time a new window closes for that customer.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_latest AS
SELECT DISTINCT ON (customer_id)
    customer_id,
    velocity_5m,
    total_spend_5m,
    avg_spend_5m,
    utilization,
    window_end AS feature_freshness_ts
FROM behavioral_features_5m
ORDER BY customer_id, window_end DESC;
