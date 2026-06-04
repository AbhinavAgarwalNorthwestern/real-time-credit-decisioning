# 03 — Models and Choices

The model layer has three modeling decisions, each with non-trivial
alternatives. This chapter is the conceptual overview; per-model details
live in `docs/model_card.md`.

## Decision 1 — Uplift modeling, not prediction modeling

Why: we care about the **causal effect of an intervention**, not the
**probability of an outcome**. See `docs/01_problem_and_domain.md`.

Concrete model class: **per-segment neural T-learners** (one model per
treatment arm; uplift = treated.predict() − control.predict()).

Why T-learner over alternatives:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **T-learner (chosen)** | Simple, transparent, easy to validate per arm | Variance bias when treatment groups are small | Acceptable at our segment sizes (1k+/segment) |
| S-learner | Single model with treatment as feature | Treatment effect can be drowned out | Rejected: too easy to under-predict uplift |
| X-learner | Uses propensity weighting for variance reduction | Two-stage; harder to debug | Considered; defer to ADR if T-learner shows variance issues |
| DragonNet | Joint propensity + outcome head; theoretical guarantees | More implementation work; harder to export to ONNX | Considered as stretch; promote if Day 2 implementation is clean |
| Causal Forest | Non-parametric; handles non-linearity | Slow inference; ONNX export non-trivial | Rejected on latency (ADR 004) |

Final decision will be documented as ADR 008 when Day 2 code is written.

## Decision 2 — Contextual bandit over uplift estimates

Uplift gives `E[reward | action, context]`. The bandit picks **which action
to take** given those estimates plus action cost and regulatory penalty.

Bandit family: **Linear contextual Thompson Sampling**, three arms
(`OFFER_CLI`, `FRAUD_CHECK`, `NOTHING`), context = feature vector +
uplift estimates.

Why Thompson Sampling over alternatives:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Linear TS (chosen)** | Exploration via posterior sampling; closed-form posterior update; arms learn online (stretch goal Day 6) | Assumes linear reward; needs prior tuning | Right for tabular features + online learning hooks |
| ε-greedy | Trivial to implement | Poor exploration efficiency; "exploration" is uniform-random | Rejected — bandit literature has dunked on ε-greedy for two decades |
| UCB1 | Provable regret bounds; no priors needed | Frequentist; harder to compose with the per-segment uplift posterior | Considered; TS chosen for the cleaner Bayesian composition |
| LinUCB | UCB with linear context | Adversarial robustness; harder posterior update | Considered; TS chosen for online-learning ergonomics |
| Deep contextual bandit | Neural reward model | Overkill at our scale; harder to interpret | Rejected — uplift NN already does the non-linearity |

Final decision will be ADR 008.

## Decision 3 — Champion-Challenger with shadow + canary

We don't deploy a new model into 100% of traffic. The promotion pipeline:

1. **Train** challenger (Metaflow `retraining_flow`, Day 5)
2. **Offline validation gate**: challenger must beat champion on a held-out
   metric AND not regress on any segment AND produce a calibrated
   prediction distribution
3. **Shadow scoring**: champion serves all traffic; challenger's would-be
   decisions are logged but not acted on (~1 hour)
4. **Off-policy evaluation** (Day 6): compute IPS / SNIPS / Doubly-Robust
   estimates of challenger reward from logged decisions
5. **Canary**: if off-policy eval is favorable, route 5% → 25% → 100% to
   challenger with auto-rollback on metric regression
6. **Audit**: 4-eyes promotion in MLflow registry (alias swap requires
   two service-account approvals; matches the spirit of SR 11-7 model
   approval committees)

Why this is in three docs:
- **The reasoning** lives in ADR 007 (when written, Day 4)
- **The runbooks** for executing rollback live in `docs/runbooks/rollback.md`
- **The off-policy estimator math** lives in `docs/06_production_patterns.md`

## What we explicitly did NOT do

- **Full Bayesian neural nets**: variance estimates would be nice but
  ort/ONNX inference doesn't sample. Reject for latency.
- **Reinforcement learning at the policy level**: would need an MDP
  formulation we can't cleanly justify given outcome lag. Bandit is the
  right shape.
- **LLM-in-the-loop for decisions**: explicitly out of scope per the
  user's "complete DS + ML eng, NOT AI eng" framing.

## Status

Concepts stable through Day 0. Day 2 implements the per-segment T-learner
and exports to ONNX. Day 3 implements the bandit. Days 4–6 build the
champion-challenger + off-policy eval loop. Each commits its own ADR.
