-- ----------------------------------------------------------------------------
-- behavioral_features_7d — 7-day tumbling features (geo signal)
-- ----------------------------------------------------------------------------
-- 7-day aggregates over events_enriched. Holds the geo-variance feature —
-- combined variance of (lat, lon) over the window. High geo variance =
-- "unstable" customer (high-risk segments per DGP § 4.2). Travel events
-- inflate variance too; that's the intended signal — both travel and
-- erratic local movement are risk indicators.
--
-- We use VAR_POP (population variance, divides by N not N-1) because each
-- window's row set is the "full population" of events in that window for
-- that customer, not a sample.
--
-- Idempotent.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_7d AS
SELECT
    customer_id,
    window_start,
    window_end,

    COUNT(*)::INT                                         AS velocity_7d,
    SUM(amount)                                            AS total_spend_7d,

    -- 2D geo spread as a scalar: sum of marginal variances of lat and lon.
    -- Equivalent to the trace of the (lat, lon) covariance matrix —
    -- captures "how spread out the customer's events are" without
    -- needing the full covariance.
    (VAR_POP(geo_lat) + VAR_POP(geo_lon))                  AS geo_variance_7d
FROM TUMBLE(events_enriched, event_time, INTERVAL '7 days')
GROUP BY customer_id, window_start, window_end;
