# 01 — Problem and Domain

## What this system does (one paragraph)

A real-time decision engine that, for every customer event in a card-issuing
bank's stream (swipes, balance checks, app opens, payment posts), decides
within 50 ms whether to **offer a credit-limit increase**, **trigger a
fraud check**, or **do nothing**. The decision is made by per-segment
neural uplift models composed with a contextual bandit selector, and the
system retrains itself when feature or prediction distributions drift.

## Why this problem is worth solving

Card issuers see ~5 000 customer events per second on the active book. For
each event there is a small action space and a sharp asymmetry between
acting and not acting:

- **Offer a CLI**: revenue if the customer would accept and spend; ties up
  capital and increases default exposure if they wouldn't
- **Trigger a fraud check**: avoids loss; adds friction that risks
  attrition
- **Do nothing**: free, but leaves money on the table on every event you
  could have acted on

The right answer depends on **who** the customer is, **what just happened**,
**what's happening in the world** (macro sentiment, rate news), and
critically **whether the intervention would CAUSE uplift** — not just
predict an outcome the customer was going to have anyway.

That last point is why this is an **uplift modeling** problem, not a
prediction problem. Predicting "this customer will spend more next month"
doesn't tell you anything if they were going to spend more anyway. Uplift
estimates `E[outcome | intervention] − E[outcome | no intervention]` —
the causal lift of acting.

## Regulatory environment (US card-issuing context)

The decisions this system makes are credit decisions under
**ECOA / Regulation B**. That imposes hard constraints:

- **Protected attributes** (race, color, religion, national origin, sex,
  marital status, age, public assistance receipt) cannot be inputs.
  Behavioral proxies for them are also off-limits.
- **Adverse-action notification** is mandated for any decline. A
  human-readable reason code must accompany the decline within 30 days.
- **Model risk management** under **SR 11-7** (Fed/OCC supervisory letter)
  governs how models in credit decisions are validated, documented,
  monitored, and retired.
- **Explainability** at the individual decision level (why this customer
  got this answer) is required for adverse actions.

The system handles these via:

- Explicit feature-allowlist enforced in `behavioral_features` (no
  protected-attribute proxies)
- SHAP delta computed per decision against a no-action baseline; logged
  to the decision audit table (consumed by adverse-action notification
  pipelines downstream)
- Model cards documenting purpose, training window, intended use, known
  limitations — see `docs/model_card.md`
- A 4-eyes promotion gate on champion-to-challenger swaps (Day 4)

## What this system is NOT

- **Not credit underwriting** — does not decide whether to issue a card or
  set the initial credit limit at application time. Those are separate
  systems with their own ECOA constraints.
- **Not fraud prevention in itself** — the fraud-check action flags an
  event for downstream fraud-system inspection; it does not approve or
  decline transactions on its own
- **Not collections** — does not decide on outstanding-balance treatment
- **Not marketing** — does not personalize creative or channel; only
  selects between three operationally-grounded actions

## Four customer-shaped questions the system can answer

| Question | Action it implies | Where in the codebase |
|----------|-------------------|-----------------------|
| Will this customer spend more if offered a CLI? | Offer CLI | `services/decisioner` (per-segment uplift NN) |
| Is this transaction anomalous enough to flag for fraud review? | Trigger fraud check | Same |
| What's the baseline expected spend if we do nothing? | Do nothing | Same — counterfactual baseline arm |
| Would the challenger model have done better than the champion on the last hour of decisions? | Promote / rollback | `services/outcome_collector` + off-policy eval on Day 6 |

## Status

This chapter is stable through Day 0. Domain assumptions reviewed once
Day 1's synthetic generator is implemented; any deviations between the
synthetic distribution and the assumptions stated here get logged in
`docs/incidents.md` and an ADR if they're architecturally relevant.
