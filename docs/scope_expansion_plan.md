# Scope Expansion Plan — Post-v1.0.0

**Authored 2026-06-29 mid-session.** Captures the agreed expansion of the
credit-decisioning project beyond the original 7-day FAANG-hardened build.
Closes credit-domain statistical gaps and remaining FAANG-production gaps
while remaining strictly cloud-agnostic (see `feedback_cloud_agnostic` memory).

This document is the **authoritative roadmap** for everything between Step 10
of `.claude/plans/sleepy-questing-sundae.md` and project v2.0.0. STATUS.md
references it; do not duplicate item text there.

## Why this expansion exists

Two honest gap analyses surfaced during the customer-attributes rollout
(session 2026-06-29):

1. **FAANG production gaps**: the project ships FAANG-level *patterns* but
   has not closed multi-region, secret rotation, bias monitoring, formal
   model risk management, cost attribution, Kafka replication, feature
   store backfill, or bandit sophistication.
2. **Credit-statistics depth gaps**: the project ships solid ML eng but
   skips the credit-domain machinery real shops (JPMC / Capital One /
   FICO) ship — calibration, vintage, Gini/KS, PD×LGD×EAD, monotonic
   constraints, WoE/IV, survival, reject inference, stress testing,
   Bayesian hierarchical PD, delayed-feedback bandits, CSI.

The user (data scientist, 11+ yrs, dual-positioning FAANG/non-FAANG)
elected to address **all 12 credit-stats items + 7 FAANG items**. Total
21 items across multiple sessions.

## Hard constraints (must hold across every item)

1. **Cloud-agnostic**: every change must remain trivially movable to AWS
   (ap-south-1 target). Base manifests cloud-agnostic; AWS-specific only
   in `deployments/overlays/aws-eks/` or `infra/terraform/`. Python
   services use `infra/lib/` abstractions, never `import boto3` directly.
2. **Bandit stays softmax in PRODUCTION** — the deep-RL alternatives in
   item 14 are research/teaching artifacts, not the deployed policy.
3. **No "Co-Authored-By: Claude" on commits.**
4. **Point-in-time correctness is non-negotiable** — every new training
   path must preserve `assert_point_in_time_correct()` invariants.
5. **Single `master_seed` → `derive_seed(namespace)`** propagation
   continues across every new RNG.
6. **Plan before code → demo + test at every checkpoint** per
   `feedback_demos_tests_checkpoints`.

## Items — full list with rationale and dependencies

Sequencing rationale: items grouped so each session has self-contained
work and produces a verifiable artifact. Credit-stats Tier S items
interlock with the ML eval notebook so they land first; FAANG items
interleave where they share infrastructure naturally.

### Phase A: finish customer attributes — re-ordered for proper ML workflow

Original Step 11 (model-eval notebook update) was misordered before Step 12
(model retrain). Real ML workflow is EDA → model → eval, so we split Step 11
into a **pre-model EDA notebook (11a)** and a **post-model eval-section update
(11b)** that sandwich Step 12 (cluster apply + retrain).

| # | Step | What ships |
|---|------|-----------|
| 1 | **Step 10** | 5 new test files (`test_customer.py`, `test_generator.py`, `test_feature_lookup.py`, `test_mv_reader.py` + extend `test_adverse_action.py`) — 30 tests green |
| 2 | **Step 11a (PRE-model EDA)** | NEW `notebooks/01_customer_eda.ipynb` — distributions per segment for each customer attribute, correlation heatmap, default-rate-by-bucket analysis, monotonicity precondition checks on raw attributes |
| 3 | **Step 12** | Lint + typecheck + test + cluster apply (rebuild producer image → DROP SOURCE CASCADE → `apply_ddl.sh` → smoke test 26 columns in `behavioral_features_serving` → retrain training pipeline → tag `v1.1.0`) |
| 4 | **Step 11b (POST-model eval)** | Replace `ml_evaluation.ipynb` Section 12 placeholder with real content: feature importance comparison behavioral-only vs +customer (uses the 26-feature model from Step 12), error analysis by credit-score buckets and tenure bands |

### Phase B: Credit-Statistics Tier S — split into pre-model and post-model

Items split by where they belong in the ML workflow:
- **Pre-model** items extend `notebooks/01_customer_eda.ipynb` (the new EDA notebook from 11a)
- **Post-model** items extend `notebooks/ml_evaluation.ipynb` Sections 13+
- **Model-side** items add training_flow library + a new model variant

| # | Item | Lands in | Library lands at |
|---|------|----------|------------------|
| 5 | **S6 WoE binning + IV ranking** (pre-model) | `01_customer_eda.ipynb` (input feature analysis) | `training_flow/woe_scorecard.py` |
| 6 | **S5 Monotonic-constrained model** (model-side) | New trained variant + comparison cell in `ml_evaluation.ipynb` | `training_flow/monotonic_gbm.py` (XGBoost with `monotone_constraints` on credit_score, tenure, income) |
| 7 | **S1 Calibration** (post-model) | `ml_evaluation.ipynb` §13 | `training_flow/calibration.py` (Hosmer-Lemeshow, Brier, calibration curves, isotonic recalibration) |
| 8 | **S2 Vintage analysis** (post-model) | `ml_evaluation.ipynb` §14 | `training_flow/vintage.py` (cohort-by-origination default curves; months-on-book vs cum-default) |
| 9 | **S3 Gini / KS / Lorenz** (post-model) | `ml_evaluation.ipynb` §15 | `training_flow/discrimination.py` |
| 10 | **S4 PD × LGD × EAD** (post-model) | `ml_evaluation.ipynb` §16 | `training_flow/loss_forecasting.py` — splits the existing profit calc into PD/LGD/EAD components; expected + unexpected loss → tag `v1.2.0` |

### Phase C: Real-time bias monitoring (FAANG 1A)

| # | Item | Lands at |
|---|------|----------|
| 10 | **Real-time bias monitor** | New service `services/bias_monitor/` consuming `decisions` + `outcomes` Kafka topics. Tracks demographic parity + equalized odds per segment, per credit-score bucket. Emits Prometheus metrics + drift events. Grafana panel. |

Why now: with calibration (S1) and monotonic constraints (S5) shipping
just before, the bias monitor has the right inputs to be meaningful.

### Phase D: Credit-Statistics Tier A — depth

| # | Item | Lands at |
|---|------|----------|
| 11 | **A7 Survival model** | `training_flow/survival.py` (discrete-time hazard model + Cox PH baseline via `lifelines`). Sibling of the T-learner. New notebook section + champion-challenger comparison. |
| 12 | **A8 Reject inference** | `training_flow/reject_inference.py` (fuzzy parceling + Heckman-style correction). Synthetic-data version simulates rejected applicants by sampling from a different generative distribution. |
| 13 | **A9 Stress testing harness** | `services/stress_test/` — DFAST-style adverse scenario harness. Shifts DGP parameters (rising rates, recession), re-scores the portfolio, reports portfolio EL under each scenario. CCAR-aligned framing. |

### Phase E: Bandit ladder + delayed feedback (FAANG 1B + Credit-Stats B11)

| # | Item | Lands at |
|---|------|----------|
| 14 | **Bandit ladder + AB harness + delayed feedback** | New notebook `notebooks/04_rl_methods_ladder.ipynb` with 7 sections: ε-greedy → softmax → LinUCB → Thompson Sampling → CQL offline RL → DQN with constructed sequential framing → **delayed-feedback variant** (provisional rewards with correction) → honest verdict. Library at `services/decisioner/src/decisioner/bandit_ladder.py`. Production decisioner unchanged. |

### Phase F: Model risk management automation (FAANG 1C)

| # | Item | Lands at |
|---|------|----------|
| 15 | **Auto model card + SR-11-7 sign-off in CI** | `services/training_flow/src/training_flow/model_card.py` (generates a model card per MLflow release with all calibration / Gini / monotonicity / stress-test results from earlier phases). `.github/PULL_REQUEST_TEMPLATE.md` SR-11-7 checklist. Auto-attached to MLflow runs. |

### Phase G: Secret rotation (FAANG 1D)

| # | Item | Lands at |
|---|------|----------|
| 16 | **External Secrets Operator + AWS Secrets Manager rotation** | `deployments/base/external-secrets/` (cloud-agnostic — supports AWS SM, GCP SM, Vault, K8s). MinIO root creds replaced with scoped svcacct. Rotation lambda stub in `infra/terraform/modules/secrets_rotation/`. |

### Phase H: Bayesian + CSI (Credit-Stats Tier B)

| # | Item | Lands at |
|---|------|----------|
| 17 | **B10 Bayesian hierarchical PD (PyMC)** | `training_flow/bayesian_pd.py` (customer × segment × time partial pooling; posterior predictive checks; portfolio-level uncertainty quantification). New notebook section. |
| 18 | **B12 CSI per-variable detector** | Add to existing `services/drift_monitor/src/drift_monitor/detectors.py` — CSI extends PSI to per-variable granularity. Tests in `services/drift_monitor/tests/test_csi.py`. |

(B11 — delayed-feedback bandit — absorbed into Phase E item 14.)

### Phase I: Kafka, FinOps, feature-store backfill (FAANG Tier 2)

| # | Item | Lands at |
|---|------|----------|
| 19 | **2A Kafka RF=3 + retention** | `deployments/overlays/aws-eks/kafka-prod-rf3.yaml` (overlay-only — dev kind stays RF=1). 7-day retention on `transactions`, `decisions`, `outcomes`. |
| 20 | **2B Cost attribution / FinOps** | `infra/terraform/modules/tagging/` (every resource tagged with `cost-center`, `model-name`, `environment`). OpenCost / Kubecost compatible. Cost-allocation doc in `docs/FINOPS.md`. |
| 21 | **2C Feature store backfill API + close live/backfill gap** | Two deliverables: (1) `services/training_flow/src/training_flow/backfill_feature.py` — recompute one feature from event history under a new version; backfill MV from a checkpoint. (2) **Close the ~15-min live/backfill gap from v1.1.0** by splitting `transactions` topic into `transactions-historical` (replay batches, chronological writes) + `transactions-live` (continuous producer). Two RW sources, `UNION ALL` in `events_enriched` MV. Watermarks track independently per source. This is the production architecture; documented in `docs/scope_expansion_plan.md` as the proper fix for the v1.1.0 known limitation. |

## Out of scope (deliberately deferred to separate projects)

| Concern | Why deferred |
|---------|--------------|
| Multi-region / DR | Requires a 2nd real EKS cluster + replication + Route53 — real spend |
| Service mesh (mTLS via Istio/Linkerd) | Significant deployment refactor; sidecar injection — separate platform project |
| Scale evidence (10k+ RPS k6) | Dev kind cluster physically cannot serve this; would need an actual scale test on AWS |
| Multi-tenant model serving | Substantial decisioner + MLflow refactor — separate "ML platform" project |

These belong in the eventual "ML platform infrastructure" project later in
the master prompt regime. Document via ADRs ("decision: out of scope for
this project") rather than attempted implementations.

## Tracking

- Progress lives in the conversational todo list, mirrored to `docs/STATUS.md`
  immediate-next-action section.
- Per-phase plans (Phase B-I) get spec docs at `.claude/plans/<phase>-<topic>.md`
  WHEN that phase starts — not preemptively.
- ADRs land for: `013-monotonic-constraints-and-regulatory-acceptance.md`,
  `014-bias-monitor-as-fairness-feedback-loop.md`, `015-bandit-ladder-research-vs-production.md`,
  `016-external-secrets-operator.md`, `017-csi-extension-to-psi.md`, etc.

## Estimated effort

Each phase = ~1-3 sessions. Total: ~18-20 sessions to v2.0.0.

| Phase | Sessions |
|-------|----------|
| A (Steps 10-12) | 3 |
| B (Credit-Stats S1-S6) | 3 |
| C (Bias monitor) | 1 |
| D (Credit-Stats A7-A9) | 3 |
| E (Bandit ladder + delayed feedback) | 2 |
| F (Model card + sign-off) | 1 |
| G (External Secrets Operator) | 1 |
| H (Bayesian + CSI) | 2 |
| I (Kafka RF=3 + FinOps + backfill) | 3 |
| **Total** | **~19** |

## Versioning

- `v1.0.0` = original 7-day production-grade build (current state minus Step 12)
- `v1.1.0` = customer attributes shipped (after Phase A)
- `v1.2.0` = Credit-Stats Tier S complete (after Phase B)
- `v1.3.0` = Bias monitor live (after Phase C)
- `v1.4.0` = Credit-Stats Tier A complete (after Phase D)
- `v1.5.0` = Bandit ladder published (after Phase E)
- `v1.6.0` = Model risk automation (after Phase F)
- `v1.7.0` = Secret rotation (after Phase G)
- `v1.8.0` = Bayesian + CSI (after Phase H)
- `v2.0.0` = Kafka RF=3 + FinOps + feature backfill (after Phase I)
