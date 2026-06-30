-- ----------------------------------------------------------------------------
-- behavioral_features_30d — 30-day tumbling features (paydown + cash-advance)
-- ----------------------------------------------------------------------------
-- Long-horizon features. Holds the two strongest p_default signals:
--   - paydown_rate_30d:    fraction of events where balance regressed
--                          (inferred via LAG in events_enriched).
--                          Direct from DGP § 2.6 — low paydown rate strongly
--                          correlates with default risk.
--   - pct_cash_advance_30d: fraction of MCC=6010 events.
--                          High-risk segments draw 14-18% cash advances
--                          vs 1-2% for low-risk (DGP § 2.5).
--
-- A T-learner that picks up these two features can disentangle the
-- "deceptively risky" group (high utilization but pays down → medium risk)
-- from the "genuinely risky" (high utilization, never pays → defaults).
--
-- Idempotent.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS behavioral_features_30d AS
SELECT
    customer_id,
    window_start,
    window_end,

    COUNT(*)::INT                                         AS velocity_30d,
    SUM(amount)                                            AS total_spend_30d,

    -- The two headline risk signals.
    AVG(is_paydown::DOUBLE PRECISION)                      AS paydown_rate_30d,
    AVG(is_cash_advance::DOUBLE PRECISION)                 AS pct_cash_advance_30d,

    -- Also useful at this horizon: average utilization over the month.
    -- Per-event utilization snapshot, then averaged.
    AVG(current_balance / NULLIF(credit_limit, 0))         AS avg_utilization_30d
FROM TUMBLE(events_enriched, event_time, INTERVAL '30 days')
GROUP BY customer_id, window_start, window_end;
