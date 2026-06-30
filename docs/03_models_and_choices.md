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

| Approach | How it works | Pros | Cons | Our verdict |
|----------|---|------|------|---------|
| **T-learner (chosen)** | Train two separate models: T₁ on treated group, T₀ on control group. Uplift = T₁(x) − T₀(x) | Simple; any base learner (PyTorch, XGBoost); each head independently auditable (SR 11-7); per-segment fits naturally (6 segments × 2 heads = 12 models) | Each model trained on *half* the data; variance bias when treatment groups are small | **Chosen.** Our 50/50 synthetic assignment eliminates the variance-bias weakness. Simplicity + auditability + base-learner flexibility wins for a 7-day build. |
| S-learner | Train 1 model with `was_treated` as an input feature. Uplift = f(x, T=1) − f(x, T=0) | Uses all data; single model | Treatment effect drowned out by stronger features; model often learns to ignore the binary treatment flag among hundreds of continuous features | Rejected — proven empirically weaker in credit uplift literature. |
| X-learner | Stage 1: T-learner. Stage 2: impute individual treatment effects using the other group. Stage 3: propensity-weighted combination | Best when treatment/control sizes are very unequal (e.g. 90/10 split) | Three-stage; harder to debug; we don't have an imbalanced split | Rejected — designed to solve a problem we don't have (our split is 50/50 by construction). |
| DR-learner (doubly robust) | Uses both a propensity model + outcome model; consistent if either is correct | Theoretically optimal bias-variance | Needs a good propensity model; harder to debug; three interconnected models | Deferred to evaluation (Day 6 OPE uses DR for *evaluation*, not for *training*). |
| DragonNet | Neural net with shared representation that simultaneously learns outcome + propensity (targeted regularization) | End-to-end neural; shared repr | Needs more data + GPU; per-segment cohorts (~10k) too small to justify | Rejected — insufficient data per segment. |
| Causal Forest (GRF) | Random forest variant with honest splitting for heterogeneous treatment effects | Non-parametric; built-in CIs; interpretable | Slow inference; doesn't export cleanly to ONNX; latency risk | Rejected on latency (ADR 004/008). |

### Five reasons we chose T-learner specifically

1. **Simplest causal architecture that still works** — two models; no
   interaction terms to debug; each head independently inspectable.
2. **Any base learner** — Day 2 uses PyTorch (small NN) per segment. If a
   segment has insufficient data, swap to XGBoost for that segment without
   changing the uplift framework.
3. **Per-segment fits naturally** — 6 segments × 2 heads = 12 models, each
   scoped and independently validatable.
4. **Our synthetic data has balanced treatment/control** — 50/50 random
   assignment eliminates T-learner's main variance weakness.
5. **Clean interview story** — "I used a T-learner because my treatment /
   control split is balanced, each head is independently auditable for
   SR 11-7, and I can use a different model architecture per segment."

### Ground truth for uplift validation — what we need per action

The fundamental problem of causal inference: **we can never observe both
what happens when we act AND when we don't act, for the same customer at
the same time.** We only see one world.

For the **CLI action** specifically, the ground-truth labels we need are:

| Label | Answers | Observable? | Source |
|---|---|---|---|
| Y₁: outcome under treatment | "They were offered CLI — did they accept? How much extra did they spend? Did they default?" | ✅ Only for customers we actually offered | Observe the treated group |
| Y₀: outcome under control | "What would they have spent *without* the offer?" | ❌ Never for offered customers | The **missing counterfactual** |
| Uplift = Y₁ − Y₀ | "Did the offer causally help?" | ❌ Never directly | Must be estimated |

### Why the "never offered" customers are the most important training data

They ARE the control group. **Without them, you cannot compute uplift.**
A customer who was going to spend $5000/month regardless shows up as a
great CLI target if you only train T₁ (the treated-outcome model). T₀
(trained on the control group) reveals they'd have spent $5000 anyway →
uplift ≈ $0 → don't waste capital on them.

The control group tells the model: **"this is what the world looks like
when we don't intervene."** Without that baseline, every model is just
predicting "who spends a lot" — which is NOT "who spends a lot BECAUSE
of our action."

### Where control-group data comes from (three sources)

| Source | How | Quality | Available? |
|---|---|---|---|
| **Historical "do nothing" decisions** | Old policy chose not to offer these customers | ⚠ Biased — old policy systematically avoided certain types | Always |
| **Randomized holdout** | 5% of decisions are uniformly random; ⅓ of those are "do nothing" | ✅ Unbiased | Only in production (costs real $) |
| **Synthetic generator** (our approach) | 50/50 random assignment; observe outcomes using embedded true response params | ✅ Unbiased by construction | Our Day 2 setup |

### Selection bias and how we address it

Historical control groups are NOT random — the bank's old policy chose
not to offer certain customers (probably the risky ones). Training T₀ on
this biased sample makes it learn "what happens to risky/low-util
customers" — not "what happens to ANY customer if not offered." This is
**selection bias** and it corrupts uplift estimates.

Our fix: because we control the synthetic generator, we assign treatment
**randomly at 50/50**. Both groups are the same size with the same feature
distribution by construction. Selection bias = zero.

In production, the fix would be:
- Propensity-weighted training (weight each control observation by
  `1 / P(not treated | x)` to rebalance)
- Or a randomized holdout (the gold standard)

Both are documented as the production deployment path in
`docs/REGULATORY_COMPLIANCE.md` (Day 7).

### Concrete training data shape for the CLI T-learner

Each row in the training dataset:

| Column | Source | Notes |
|---|---|---|
| `customer_id` | transactions | join key |
| `features` (velocity, utilization, MCC entropy, …) | `behavioral_features_latest` MV | point-in-time features as-of the decision moment |
| `was_treated` | historical decisions or synthetic assignment | 1 = offered CLI; 0 = not offered |
| `outcome_accepted` | outcomes topic (~14 days) | 1 if accepted |
| `outcome_spend_delta_30d` | outcomes topic (~30 days) | $ spend difference vs prior 30 days |
| `outcome_defaulted_12m` | outcomes topic (~12 months) | 1 if defaulted within outcome horizon |

T₁ trains on `was_treated == 1` rows → predicts `outcome_spend_delta_30d`.
T₀ trains on `was_treated == 0` rows → predicts `outcome_spend_delta_30d`.
Uplift = T₁(x) − T₀(x).

### At serving time — both heads run on every customer

```
Customer arrives with features x

T₁(x) = $450/month predicted spend if offered
T₀(x) = $380/month predicted spend if NOT offered
Predicted uplift = $70/month

Expected profit = uplift × margin − default_risk × LGD − capital_cost
Bandit picks action with highest expected profit
```

Both T₁ AND T₀ evaluate every customer — even "obvious offer" targets.
Because sometimes T₀ says "they'd spend $450 anyway" → uplift ≈ $0 →
the offer wastes capital.

### Baselines we measure the neural T-learner against

A headline interview claim of the form "neural T-learner adds business
value" requires an actual measured lower bound. Day 2 trains and reports
on the following baselines alongside the neural model:

| Baseline | What it is | What it proves if neural beats it |
|---|---|---|
| **Always-offer constant policy** | Offer CLI to every customer | Neural can identify *which* customers to skip |
| **Never-offer constant policy** | The "do nothing" arm from the cost-benefit table in `01_problem_and_domain.md` | The neural-driven actions add value over inaction |
| **Random 50/50 policy** | Offer with p=0.5 independent of features | The neural uses features informatively, not just noise |
| **Logistic-regression T-learner** | Same T-learner framework, linear base models per arm | **The most important comparison** — isolates whether *neural capacity* (vs feature engineering or RCT data) is the source of any lift. If linear ≈ neural, the neural net isn't earning its keep and we ship the linear model. |

Reporting: Kendall τ between predicted uplift and the analytically-known
true uplift; simulated profit per decision over a held-out horizon. MLflow
parent run logs all five (4 baselines + 1 neural) as comparable runs;
champion alias goes to the highest-profit model, not by default to neural
(honest reporting per the SR 11-7 spirit).

### Treatment assignment for Day-2 training data

Synthetic RCT — `T ~ Bernoulli(0.5)` independent of features, per
**ADR 010**. Biased-log handling (IPW / SNIPS / DR estimators) is
deliberately deferred to Day-6 OPE, where it is the actual demonstrated
capability. See the "Selection bias and how we address it" section above
and ADR 010 for the full reasoning.

### Day 2 measured results (2026-06-21)

Pipeline ran with `--master-seed 42 --n-optuna-trials 5` on 34,891 rows
(1000 customers, 6 segments, 27 features).

**Baselines:**

| Policy | Simulated profit/decision | Kendall τ vs true uplift |
|--------|---------------------------|--------------------------|
| Always-offer | $88.22 | n/a |
| Never-offer | $0.00 | n/a |
| Random 50/50 | $41.85 | n/a |
| **Logistic T-learner** | **$302.05** | **0.43** |

**Neural T-learner (per segment):**

| Segment | Kendall τ |
|---------|-----------|
| seg0 (low_risk_tenured) | 0.1138 |
| seg1 (med_risk_tenured) | 0.1815 |
| seg2 (high_risk_tenured) | 0.1638 |
| seg3 (low_risk_new) | 0.1197 |
| seg4 (med_risk_new) | 0.0191 |
| seg5 (high_risk_new) | 0.0048 |
| **Mean** | **0.1005** |

**Champion: baseline_logistic_t_learner** (τ=0.43 vs neural mean
τ=0.1005). This is honest reporting — the linear model won. The neural
ONNX artifacts are still registered in MLflow (version 1) and both models
run at inference time for shadow scoring per the Day 4 design.

**Why logistic beat neural (5 Optuna trials):** With only 5 HPO trials on
a CPU-only kind cluster, the neural nets had insufficient tuning budget.
The logistic model's inductive bias (linear decision boundary) is a
better fit for the data size per segment (~5,800 rows each, split 50/50
into ~2,900 per treatment arm). This is the expected outcome for small
cohorts — and reporting it honestly is the point.

**Interview framing:** "The logistic T-learner outperformed the neural
net on Kendall τ with true uplift (0.43 vs 0.10). I shipped the linear
model as champion because SR 11-7 requires honest model comparison, not
default-to-neural. The neural model stays registered for shadow scoring —
if the data grows or HPO budget increases, it can be re-evaluated via the
champion-challenger pipeline."

## Decision 2 — Contextual bandit over uplift estimates

Uplift gives `E[reward | action, context]`. The bandit picks **which action
to take** given those estimates plus action cost and regulatory penalty.

Bandit family: **Softmax over expected profits** (temperature-scaled),
three arms (`OFFER_CLI`, `FRAUD_CHECK`, `NOTHING`), context = feature
vector + uplift estimates.

Why softmax bandit (not full RL) over alternatives:

| Approach | Pros | Cons | Verdict |
|----------|------|------|---------|
| **Softmax bandit (chosen)** | Exploration via temperature; simple; deterministic given seed; probability of each arm is interpretable as propensity for OPE logging | No posterior update; temperature is a hyperparameter | Right for our use case — uplift model does the heavy lifting, bandit just selects |
| ε-greedy | Trivial to implement | Poor exploration efficiency; "exploration" is uniform-random | Rejected — bandit literature has dunked on ε-greedy for two decades |
| Thompson Sampling | Exploration via posterior sampling; closed-form posterior update | Assumes linear reward; needs prior tuning; harder to log propensity deterministically | Considered; softmax chosen for simpler propensity logging |
| UCB1 | Provable regret bounds; no priors needed | Frequentist; harder to compose with the per-segment uplift posterior | Considered |
| Full RL (MDP) | Optimal for sequential decisions with delayed reward | Outcome lag (14–365 days) makes MDP state transitions unjustifiable; credit decisions are contextual bandits, not MDPs | **Rejected** — outcome lag means we cannot observe state transitions at decision time |
| Deep contextual bandit | Neural reward model | Overkill at our scale; harder to interpret | Rejected — uplift NN already does the non-linearity |

### Why bandit and not full RL

The user asked: "all models or data don't have counterfactuals so why RL
here?" The answer: **we don't use RL.** A contextual bandit is the right
abstraction because:

1. **No sequential state** — each credit decision is independent given
   the customer's current features. There is no "state transition" from
   one decision to the next that we need to model.
2. **Massive outcome lag** — we learn whether a CLI offer was good 14–365
   days later. An MDP would need to assign credit to actions taken months
   ago, which is intractable without strong assumptions.
3. **Bandit = one-step decision** — "given this customer's features and
   uplift estimates, which action maximizes expected profit right now?"
   That's exactly what a bandit does.
4. **The uplift model handles the hard part** — the T-learner estimates
   E[reward | action, context]. The bandit just picks argmax (with
   exploration). No need for value functions, policies, or Bellman
   equations.

The softmax temperature controls exploration: higher temperature = more
uniform (more exploration), lower = more greedy. Propensity = softmax
probability, logged with every decision for Day 6 OPE.

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

Day 2 complete (2026-06-21): per-segment T-learner trained, ONNX exported,
MLflow registered. Champion = logistic T-learner (τ=0.43 > neural 0.10).
Day 3 in progress: decisioner deployed to kind cluster with softmax
bandit. Days 4–6 build the champion-challenger + off-policy eval loop.
