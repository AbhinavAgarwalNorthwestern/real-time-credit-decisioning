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

## Optimization target — what the system maximizes

The decisioner picks the action that maximizes **expected profit per
customer event**:

```
maximize  E[profit | action, context]

profit(action, context) = revenue(action, context)
                        − expected_loss(action, context)
                        − capital_cost(action, context)
                        − friction_cost(action, context)
```

`argmax_a E[profit | a, x]` where `x` is the feature vector from the
RisingWave materialized view. This is **uplift** (causal effect of
acting vs not), not raw outcome prediction — which is why Day 2 trains
T-learners (causal-inference model class), not plain classifiers.

## Cost-benefit per action — concrete numbers

Assumptions calibrated to public consumer-credit aggregates (cited in
`docs/data_card.md` Day 2):

| Component | Value | Source |
|---|---|---|
| Net interest margin on new credit | 12% APR on revolving balance | typical card NIM |
| Probability of CLI acceptance (avg segment) | 25% | industry benchmark |
| Expected ΔSpend given accept | $400/month × 12 months | conservative |
| Probability of default over 12 months (avg segment) | 4% | Visa/MC aggregate |
| Loss given default | 80% of exposure | card LGD typical |
| Cost of capital (annual) on regulatory capital held | 8% | Basel III approximation |
| Regulatory capital required | 8% of additional credit exposed | mid-tier card book |
| Avg fraud loss per confirmed fraud | $500 | small-ticket fraud average |
| Probability of fraud (avg event) | 0.05% | industry incidence |
| Probability of fraud given high-risk features | 5% | when behavioral signals trigger |
| Cost of false-positive fraud check (declined legit txn) | $40 friction + churn risk | empirically calibrated |
| Discount rate over outcome horizon | 0.95 | 1-year horizon |

**Worked example** for a "low risk × tenured" segment customer with
$2000 credit limit and $500 current balance:

| Action | Computation (per-event) | E[profit] |
|---|---|---|
| Offer CLI of $500 | +25% × ($400/mo × 12 × 12% × 0.95) − 4% × $500 × 80% − 8% × $500 × 8% | **+$112.80** |
| Trigger fraud check | +0.05% × $500 − 99.95% × $40 | **−$39.72** |
| Do nothing | 0 | **$0** |

Bandit picks **Offer CLI**. Net value of this single decision over the
no-action baseline: **+$112.80**.

At scale (20M events/day for a mid-sized issuer), with the calibration
above, the bandit policy captures expected lift of **~$80M-$120M
annually** over a do-nothing baseline. That number is the headline
metric Day 7 reports — the OPE estimate with bootstrap CI.

## KPIs — what we measure and when

| Metric | Type | Day measured | Target |
|---|---|---|---|
| Off-policy estimated lift over baseline (IPS, SNIPS, DR with bootstrap CI) | $$$ value | Day 6 | Lift > 0 with 95% CI above 0 |
| Per-segment uplift AUC | Model quality | Day 2 | > 0.65 per segment |
| Model calibration (Brier score) | Model quality | Day 2 | < 0.10 |
| Decision latency p99 | SLO | Day 7 (k6) | < 50 ms at 5k RPS |
| Decision distribution drift (PSI) | Operational | Day 5 | PSI < 0.2 trigger |
| Adverse-action reason-code coverage | Compliance | Day 6 | 100% of declines |
| Champion-challenger promotion latency | Operational | Day 4 | < 1 hour end-to-end |

## How we know the "right" action in training data

In production at a real bank you can never directly observe "would this
customer have accepted CLI if we hadn't asked?" — that's the
fundamental problem of causal inference. In *our* synthetic training
data we **embed the ground truth**: every customer in
`services/transactions/src/transactions/customer.py` carries three
true response parameters:

- `true_p_accept_cli` — the customer's true probability of accepting an offered CLI
- `true_delta_spend_if_accept` — the customer's true incremental spend if they accept
- `true_p_default` — the customer's true probability of defaulting on new credit exposure

From those three numbers we can compute analytically what the true
expected profit of each action would be — so the "right answer" per
row is *known by construction*. The model's job is to learn a function
`features → predicted_uplift` from observable signals; validation
checks that predicted uplift orders customers correctly relative to the
true uplift we computed.

Three production-grade validation mechanisms (`docs/04_results_and_metrics.md`
documents all five we considered):

| Mechanism | What it gives | In our build |
|---|---|---|
| RCT randomized holdout (5% of decisions get random actions) | Unbiased ground-truth lift estimate | **Documented** (`docs/REGULATORY_COMPLIANCE.md`); not built — costs real $ |
| Off-policy evaluation (IPS / SNIPS / DR) | Bias-free lift estimate of a new policy from logged decisions | **Built Day 6** |
| T-learner imputation under ignorability | Predicted uplift assuming no unobserved confounders | **Built Day 2** |

## Status

This chapter is stable. Specific numeric assumptions in the cost-benefit
table get refined on Day 2 against `data_card.md` calibration; if any
assumption changes materially, the row gets a citation footnote and the
worked-example number is recomputed.
