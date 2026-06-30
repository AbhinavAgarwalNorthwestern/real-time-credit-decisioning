# 02 — Data and Features

Documents the synthetic transaction schema, the behavioral features
computed in stream, and the RisingWave materialized-view definitions
that make this the system's feature store (per ADR 002).

Detailed dataset documentation lives in `docs/data_card.md` (Google
Data Cards Playbook format). Detailed model-input documentation lives
in `docs/model_card.md`. This chapter is the conceptual overview.

## Transaction event schema (synthetic; produced by `services/transactions`)

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | UUID | Synthetic; unique per event |
| `customer_id` | string | Synthetic; ~10k cohort by default |
| `timestamp_ms` | int64 | Epoch ms; monotonic per customer |
| `amount` | float | Dollars |
| `mcc` | int | Merchant category code (4-digit ISO) |
| `merchant_id` | string | Synthetic |
| `geo_lat`, `geo_lon` | float | Synthetic; clustered around customer home |
| `is_card_present` | bool | |
| `current_balance` | float | Account balance after this event |
| `credit_limit` | float | Current limit |
| `segment_id` | int | Customer segment (0–5); see `segments.py` |
| `payload_version` | int | Schema-evolution marker |

## Behavioral feature view (computed by RisingWave materialized views per ADR 009)

These features are computed by `CREATE MATERIALIZED VIEW` DDL across seven
files in `deployments/dev/risingwave/`. There is no Python feature service —
RisingWave's streaming SQL is the feature engine. The decisioner queries
the resulting MV via the Postgres protocol at request time. ADR 002 +
ADR 009 explain why.

### Plain-language overview

Imagine every customer has a live "behavioral dashboard." The ML model
reads that dashboard to decide an action. But behavior at different
horizons tells different stories — last 5 minutes captures "are they
mid-session?" while last 30 days captures "do they pay down their
balance?" So we maintain **one dashboard per horizon**, plus one
"stitched" dashboard for the decisioner to read at decision time:

| Horizon | What it captures |
|---|---|
| **5 minutes** | Mid-session burst — are they spending right now? |
| **1 hour** | Spending mix — focused (one MCC) or scattered (many MCCs)? |
| **24 hours** | Late-night impulse share + interarrival cadence |
| **7 days** | Geographic spread — stable or roaming? |
| **30 days** | Headline risk signals — paydown rate + cash advance share |
| **Serving** | Latest of all the above stitched into one row per customer |

For Day-2 *training*, the data builder queries each per-window MV
directly (not the serving MV) so it can recover features as-of any
historical moment, not just "now."

### Feature catalogue

| Feature | File | Window | What it tells the model | Why it matters |
|---|---|---|---|---|
| `velocity_5m`, `total_spend_5m`, `avg_spend_5m`, `utilization` | `01_mv_behavioral_features.sql` | 5m TUMBLE | Short-burst intensity + current utilization at end of window | Session detection; util correlates with both p_accept and p_default |
| `velocity_1h`, `total_spend_1h`, `avg_spend_1h`, `mcc_entropy_1h` | `03_mv_behavioral_features_1h.sql` | 1h TUMBLE | Hourly intensity + Shannon entropy over the MCC mix | High entropy = scattered/impulsive spending; DGP § 2.5 risk signal |
| `velocity_24h`, `total_spend_24h`, `avg_spend_24h`, `pct_late_night_24h`, `avg_interarrival_24h` | `04_mv_behavioral_features_24h.sql` | 24h TUMBLE | Daily activity + share of 22:00–04:00 events + average gap between events | Late-night spending = impulsive (high-risk peaks per DGP § 3.1) |
| `velocity_7d`, `total_spend_7d`, `geo_variance_7d` | `05_mv_behavioral_features_7d.sql` | 7d TUMBLE | Weekly activity + `VAR_POP(lat) + VAR_POP(lon)` over the week | Geographic instability = travel + erratic local movement = risk |
| `velocity_30d`, `total_spend_30d`, `paydown_rate_30d`, `pct_cash_advance_30d`, `avg_utilization_30d` | `06_mv_behavioral_features_30d.sql` | 30d TUMBLE | The two strongest p_default signals + long-horizon utilization | Paydown rate = anti-default; cash advances = financial stress (DGP § 2.6) |
| Per-event flags: `is_paydown`, `is_cash_advance`, `is_late_night`, `time_since_last_s`, `prev_balance` | `02_mv_events_enriched.sql` | per-event | LAG-derived signals reused by the windowed MVs | Single home for LAG logic — avoids duplicating it across windows |

### Two non-obvious computations

**Paydown detection — silent in the generator, recovered by LAG.** The
producer doesn't emit a separate "paydown" event; per `generator.py:166-175`,
when a paydown fires the customer's stored balance is silently reduced and
the *next* transaction shows a lower `current_balance` than the previous
one. Non-paydown events always increase or hold balance (amounts are
positive, balance is capped at `credit_limit`). So:

```sql
is_paydown = (LAG(current_balance) OVER (PARTITION BY customer_id
                                         ORDER BY event_time)
              > current_balance)
```

`paydown_rate_30d = AVG(is_paydown)` over the 30-day window.

**MCC entropy — two-stage SQL.** Shannon entropy
`H = -SUM(p_i * ln(p_i))` requires per-MCC probabilities, but a single
`GROUP BY (customer, window)` collapses MCCs together. So we stage:

1. `mcc_counts_1h` — `GROUP BY (customer, window, mcc)` produces per-MCC counts
2. `behavioral_features_1h` — joins `mcc_counts_1h` to its window-total and computes `-SUM((cnt/total) * LN(cnt/total))`

State cost is bounded by ~12 MCC codes × closed windows.

## Decision audit schema (written by `services/decisioner`)

For every `/decide` call, one row is queued to Kafka topic `decisions`
and downstream-materialized into RisingWave for the off-policy eval flow.

| Field | Type | Used for |
|-------|------|----------|
| `decision_id` | UUID | Primary key |
| `customer_id` | string | Join key for outcomes |
| `decision_ts_ms` | int64 | Latency analysis + drift windowing |
| `segment` | enum | Segment routing audit |
| `champion_model_uri` | string | Model lineage |
| `challenger_model_uri` | string | What the challenger would have done |
| `champion_action` | enum | `OFFER_CLI` / `FRAUD_CHECK` / `NOTHING` |
| `champion_propensity` | float | Probability champion picked this action |
| `challenger_action` | enum | Counterfactual |
| `feature_vector_hash` | bytes | Reproducibility + replay |
| `shap_delta_baseline` | json | Per-feature attribution vs no-action baseline |
| `regulatory_flags` | json | ECOA-relevant flags (e.g. adverse-action eligibility) |

## Outcome event schema (consumed by `services/outcome_collector`)

| Field | Type | Notes |
|-------|------|-------|
| `outcome_id` | UUID | |
| `decision_id` | UUID | Foreign key to decisions |
| `outcome_ts_ms` | int64 | |
| `outcome_kind` | enum | `CLI_ACCEPTED`, `CLI_REJECTED`, `FRAUD_CONFIRMED`, `SPEND_DELTA_30D`, `CHURNED` |
| `outcome_value` | float | For continuous outcomes (e.g. spend delta) |

## Why RisingWave is the feature store (and not Feast)

Documented in ADR 002. Short version: MV-over-CDC semantics give us
point-in-time-correct training joins and sub-10 ms online lookups
without an additional service. SQL is the schema language.

## Data-generating process (DGP) — synthetic generator design

Full specification in **`docs/dgp_design.md`**. Summary below.

The DGP implements a **heap-based merged non-homogeneous Poisson process**
across 6 customer segments (`{low,med,high} risk × {tenured,new}`).

### Architecture

```
segments.py  → frozen dataclass with all per-segment distribution params
customer.py  → generate_cohort(): assigns segments, draws all per-customer params
generator.py → min-heap of (next_event_ms, customer_idx); always emits earliest
distributions.py → sampling mechanics (MCC, amount, geo, circadian, day-of-week)
backfill.py  → historical replay consuming from the heap
```

### Key design choices

1. **Per-customer Poisson rates from Gamma(α_s, β_s)** — rate heterogeneity
   within segments produces meaningful `velocity_*` feature variance
2. **Circadian + day-of-week modulation** — double-peaked sinusoid with
   segment-specific peak hours; high-risk peaks later (impulsive patterns)
3. **Session bursts** — after emitting, probability `p_session` of
   entering a burst period (3–5× rate boost for 10–30 min)
4. **Per-customer MCC weights from Dirichlet** — cash advance % (MCC 6010)
   is the strongest risk signal (1% for low-risk, 18% for high-risk new)
5. **Segment-specific paydown frequency** — drives the `paydown_rate`
   feature that anti-correlates with default
6. **Context-dependent response functions** — `true_p_accept` and
   `true_p_default` vary with current utilization and velocity (see
   `docs/dgp_design.md` §5), making real-time decisions valuable over batch

### Why the ML problem is hard

| Trap | What happens | What the good model learns |
|---|---|---|
| High-risk customers accept CLI most often (0.40–0.47) | Naive model offers to high-risk | Acceptance × default × exposure makes high-risk unprofitable |
| Utilization correlates with BOTH acceptance and default | Feature is ambiguous alone | Model must condition on paydown + velocity jointly |
| Medium-risk tenured is marginal | Small errors lose money | Precision here drives business value delta |

### Ground-truth response functions (embedded; recovered by models)

For every customer, the data generator carries three "true" parameters
that the bandit / uplift models try to recover:

- `true_p_accept_cli(x)` — probability the customer accepts a CLI offer
- `true_delta_spend_if_accept(x)` — incremental monthly spend if they accept
- `true_p_default(x)` — probability of default within the outcome horizon

At training time, **outcome events are synthesized using these
parameters** (per `docs/06_production_patterns.md` Pattern 10 — synthetic
shortcut for the long-horizon label problem). The trained model's quality
is then measured by comparing its predicted uplift against the analytically-
computable true uplift, customer by customer. See `docs/04_results_and_metrics.md`
for the validation methodology.

## Status

Schema stable. DGP fully implemented Day 1 Phase D-2 with all 6 segments,
temporal modulation, session bursts, and per-customer Dirichlet MCC weights.
Day 2 may refine distribution parameters after measuring validation criteria
in `docs/dgp_design.md` §9. Any schema drift gets a new `payload_version`
rather than mutating the existing one.
