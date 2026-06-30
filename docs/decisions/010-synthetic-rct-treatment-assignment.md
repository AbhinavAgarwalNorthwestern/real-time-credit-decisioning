# ADR 010: Synthetic RCT for Day-2 training-data treatment assignment

**Status:** Accepted
**Date:** 2026-06-07
**Decision makers:** Platform owner

## Context

Day 2 trains per-segment T-learner uplift models (per ADR 008 architecture
and the design in `docs/03_models_and_choices.md`). Every training row
must take the shape:

```
(X = behavioral features at moment t,
 T = was CLI offered? 0/1,
 Y = observed profit outcome)
```

The model learns `uplift(X) = E[Y | X, T=1] − E[Y | X, T=0]`. For this to
be learnable, the dataset must contain examples of both arms for similar
X — i.e. it needs a defensible **treatment-assignment mechanism**.

Two implementations were possible:

- **Option A — Synthetic RCT**. For each (customer, window_end) row from
  the RisingWave `behavioral_features_5m` MV, draw `T ~ Bernoulli(0.5)`
  independent of features. Simulate Y from the customer's embedded true
  response parameters given T.
- **Option B — Simulated biased logging policy + IPW correction**.
  Hand-code a realistic biased policy (e.g. high-utilization customers
  get offered CLI 2× more often). Use that policy to assign T. At
  training time, weight each row by `1 / P(T=t | X)` (inverse propensity
  weighting) to recover an unbiased estimator.

Both produce the same training-table shape. The question is which best
serves the Day-2 demonstration and the broader 7-day build.

## Decision

We adopt **Option A — synthetic RCT** for Day-2 training-data treatment
assignment.

The training-data builder (`services/training_flow/src/training_flow/data_builder.py`)
draws `T ~ Bernoulli(0.5)` per row and simulates outcomes from the
customer's embedded response parameters. No propensity model is involved
in Day-2 training.

The bias-correction machinery (IPS, SNIPS, doubly-robust estimators)
ships on **Day 6** as part of the off-policy evaluation harness, where it
is the actual capability being demonstrated.

## Consequences

### Positive

- **Selection bias is zero by construction.** Both arms have the same
  feature distribution; the model can recover the causal uplift cleanly.
  This is the property `docs/03_models_and_choices.md` § "Selection bias
  and how we address it" assumes.
- **Matches the gold-standard real-bank practice.** Production card
  issuers (JPM, Capital One, Discover — cited in
  `docs/01_problem_and_domain.md`) carve out 5–10% RCT exploration budgets
  *specifically* to produce clean uplift-training data. Our synthetic RCT
  mirrors that established practice.
- **Cleaner separation of concerns between Day 2 and Day 6.** Day 2
  answers "can the model learn the right uplift from clean data?" Day 6
  answers "can the OPE harness estimate a new policy's profit from biased
  logs?" Mixing biased training data into Day 2 would muddle both demos
  — a reviewer couldn't tell which capability was being validated where.
- **Less code on the hot path.** ~200 fewer lines of weighting + propensity
  modelling in `data_builder.py` and `train.py`.
- **Lower training-time variance.** IPW-weighted estimators have higher
  variance than unweighted on equivalent data (the effective sample size
  after weighting is smaller). Synthetic RCT gives us the full N.

### Negative

- **Training data doesn't mirror what production logs will look like.**
  Production champion-model decisions will be biased; Day-2 training data
  is not. The Day-6 OPE harness is the system component that handles
  that gap — but the gap is real and worth naming.
- **No Day-2 exercise of propensity modelling.** If the platform later
  retrains on observational logs (Day 5 retraining_flow with production
  logs), it will need a propensity head that Day 2 doesn't build. Day 6
  builds it for evaluation; retraining will inherit from there.
- **The Day-2 model's calibration to production data is unverified at
  Day-2 close.** "Beats baseline on synthetic RCT" doesn't entail "beats
  baseline on biased production logs." This is mitigated by the Day-6
  OPE harness, which is the actual production-readiness checkpoint, and
  by the runbooks in `docs/runbooks/`.

## Why this isn't a general "RCT > observational for training"

It's specific to a synthetic-data portfolio build:

- **We control the data-generating process.** Whatever bias we invent
  for Option B, we'd correct for using the same propensity function we
  just invented — Option B converges to Option A in expectation with
  added variance. The "realism" of biased synthetic data is illusory
  because there is no unknown bias to discover.
- **Real production demands IPW because real bias is unknown.** Champion
  policies + manual overrides + outage windows + phased rollouts produce
  a propensity that has to be *estimated* from logs. That estimation is
  the hard part — and it's exactly what Day 6 demonstrates against a
  *simulated production policy* whose true propensity we deliberately
  withhold from the evaluator.
- **At Day 2 we want clean signal, not realism.** "Can the architecture
  learn?" is the question. Realism enters Day 6.

If this were a real production system retraining on its own logs, we'd
need IPW at training time — but then we'd also need a real propensity
estimator (not the toy generator), and the whole thing becomes a
different project.

## Alternatives considered

- **Option B — Biased simulated policy + IPW correction** (above).
  Rejected: the bias is self-generated, so the IPW correction recovers
  Option A's distribution in expectation while adding code and variance.
- **No randomization — observational simulation only** (deploy a deterministic
  policy, log only what it chose). Rejected: with `T` determined by `X`,
  the control-group counterfactual is unobservable for treated rows and
  vice versa; uplift becomes unidentifiable without strong assumptions.
- **Stratified RCT by segment** — separate Bernoulli(0.5) draw per
  segment. Considered but not adopted: a single Bernoulli(0.5) across the
  cohort already produces balanced arms per segment in expectation
  because segment membership is independent of the coin flip. Stratification
  would add code for no statistical gain at our cohort size (~1k customers
  × 30-day backfill).

## Related

- ADR 002 — RisingWave as feature store. Provides the point-in-time-correct
  feature snapshots that training reads.
- ADR 009 — Pure RisingWave SQL for feature computation. The MV rows we
  query for training are produced by that pipeline; no Python feature
  re-implementation at training time.
- `docs/03_models_and_choices.md` § "Selection bias and how we address
  it" — extended explanation, including a comparison of historical /
  randomized / synthetic control-group sources.
- `docs/01_problem_and_domain.md` — cites the production 5% RCT carve-out
  pattern this ADR mirrors.
- `docs/dgp_design.md` § 9 — the validation criteria the Day-2 training
  pipeline gates against (validates the *data*, separately from the model
  learning the *treatment effect*).
- **Day 6 OPE harness** — `services/outcome_collector/` + the off-policy
  estimators (IPS, SNIPS, DR with bootstrap CI). The biased-log machinery
  this ADR defers to.
