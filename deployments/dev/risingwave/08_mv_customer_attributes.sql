-- ----------------------------------------------------------------------------
-- customer_attributes — one row per customer with their constant attributes
-- ----------------------------------------------------------------------------
-- The 5 customer-level attributes (credit_score, annual_income,
-- account_tenure_months, n_products, prev_delinquency_count) are denormalized
-- onto every transaction event by the producer (services/transactions),
-- because all values are constant per customer and Kafka has no separate
-- customer profile topic in this design.
--
-- This MV de-denormalizes them: it computes one row per customer using MAX()
-- as a no-op aggregator (every event for the same customer carries identical
-- values, so MAX is safe and lets us aggregate without an arbitrary pick).
--
-- The serving MV (09_mv_behavioral_features_serving) joins this MV onto the
-- per-customer behavioral feature snapshot, so /decide gets a single row with
-- both behavioral and customer attributes. Numbered 08_ so this MV is created
-- before the serving MV (09_) — apply_ddl.sh runs files in lexical order.
--
-- Unlike the other MVs, this is NOT windowed — there's no window_end column.
-- Training-side join code (services/training_flow/.../mv_reader.py) treats
-- non-windowed MVs as a simple left-join on customer_id (no merge_asof).
--
-- Idempotent.
-- ----------------------------------------------------------------------------

CREATE MATERIALIZED VIEW IF NOT EXISTS customer_attributes AS
SELECT
    customer_id,
    MAX(credit_score)            AS credit_score,
    MAX(annual_income)           AS annual_income,
    MAX(account_tenure_months)   AS account_tenure_months,
    MAX(n_products)              AS n_products,
    MAX(prev_delinquency_count)  AS prev_delinquency_count
FROM transactions
GROUP BY customer_id;
