-- ----------------------------------------------------------------------------
-- behavioral_features_24h — 24-hour tumbling features
-- ----------------------------------------------------------------------------
-- Aggregates over events_enriched at 24-hour grain. Holds the late-night-share
-- feature — high-risk segments (4-5) have late evening / midnight peaks per
-- DGP § 3.1.
--
-- Velocity_24h is the rate feature the T-learner uses for short-horizon
-- intensity; combined with velocity_5m it lets the model distinguish "in a
-- session right now" (high 5m, moderate 24h) from "active all day"
-- (moderate 5m, high 24h).
--
-- Idempotent.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_24h AS
SELECT
    customer_id,
    window_start,
    window_end,

    COUNT(*)::INT                                         AS velocity_24h,
    SUM(amount)                                            AS total_spend_24h,
    AVG(amount)                                            AS avg_spend_24h,

    -- Share of events in 22:00-04:00 UTC. Computed in events_enriched as
    -- a 0/1 flag; we average it here.
    AVG(is_late_night::DOUBLE PRECISION)                   AS pct_late_night_24h,

    -- Average interarrival in the window. Capture the "in-session burst"
    -- vs "spread across the day" pattern.
    AVG(time_since_last_s)                                 AS avg_interarrival_24h
FROM TUMBLE(events_enriched, event_time, INTERVAL '24 hours')
GROUP BY customer_id, window_start, window_end;
