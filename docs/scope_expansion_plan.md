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

### Phase K: 5k+ RPS load test with SLO compliance evidence — added 2026-06-30 (FAANG-undeniable demo 1)

**Goal**: produce a 5k+ RPS sustained load test report with documented SLO
compliance, recorded as a screen-capture demo. This becomes a concrete
interview hook to land L5/L6 ML eng roles.

**What an interviewer sees** (10-minute walkthrough):

- A Grafana dashboard with **p50 / p95 / p99 latency** during a 30-minute
  sustained 5,000 RPS run against the decisioner
- **Error rate < 0.1%**
- **Throughput stays above 5,000 RPS** even during a 200% spike
- A **k6 summary report** exported as JSON
- A **terraform-managed cluster** that scaled up + down (cost report)
- A **written incident** documenting one failure mode discovered during
  the test (real prod load tests always surface at least one)

**Items**:

| # | Item | Lands at |
|---|------|----------|
| 22 | Scale node group to 10× m6i.xlarge (40 vCPU, 160 GB RAM) | `infra/terraform/modules/eks_cluster/main.tf` — temporary edit, tear-down after |
| 23 | Install Prometheus + Grafana stack via in-cluster Helm Job | `deployments/dev/eks/install_observability_job.yaml` |
| 24 | Apply decisioner Grafana dashboard configmap + Grafana sidecar | `dashboards/decisioner.json` (already exists) |
| 25 | Wire decisioner HPA: min=3, max=20, target CPU 60% | `deployments/base/services-finance/decisioner/hpa.yaml` (NEW) |
| 26 | Decisioner PodDisruptionBudget: minAvailable=2 | `deployments/base/services-finance/decisioner/pdb.yaml` (NEW) |
| 27 | Decisioner resource tuning: 1 CPU / 2 Gi requests | edit `deployments/base/services-finance/decisioner/deployment.yaml` |
| 28 | RW compute scale to 3 via Helm values | `--set compute.replicas=3` in install job |
| 29 | ALB ingress for decisioner via AWS Load Balancer Controller | `deployments/overlays/aws-eks/decisioner-alb.yaml` (NEW) |
| 30 | External k6 driver on a separate EC2 instance (m5.xlarge) | `scripts/run_load_test_aws.sh` (NEW) |
| 31 | Modified k6 stages (1k→2.5k→5k→7.5k→cool) | edit `scripts/load_test.js` |
| 32 | Grafana dashboard PNG exports + JSON report | `docs/LOAD_TEST_RESULTS.md` (replace stub) |
| 33 | Recorded 5-min screen capture of the run | `docs/demos/load_test_v2.0.1.mp4` |

**Effort**: ~3 sessions. **Cost**: ~$45 total across 3 sessions
(scaled-up cluster + ~6h k6 driver + Grafana export). **Tag**: `v2.0.1`
(load-test verified).

**Realistic SLO targets** at 5k RPS sustained:

| Metric | Target | Why credible |
|---|---|---|
| p50 latency | < 15 ms | v1.0.0 warm = 7 ms; at 10× load expect 2x |
| p95 latency | < 30 ms | Standard ML serving target |
| **p99 latency** | **< 50 ms** | **Headline number for the demo** |
| Error rate | < 0.1% | Circuit breaker + retry handles |
| Throughput floor | 5,000 RPS | Whole point of the test |
| RW feature lookup p99 | < 10 ms | Hummock cache + connection pool |

If we miss any → equally good interview material: "we found this
bottleneck, here's how we'd fix it."

---

### Phase L: Bandit A/B test with statistical lift validation — added 2026-06-30 (FAANG-undeniable demo 2)

**Goal**: produce a statistically-rigorous A/B test report demonstrating
that the bandit policy can be evaluated against a challenger on real
synthetic traffic with bootstrap CI on lift. Recorded as a 15-minute
walkthrough.

**What an interviewer sees**:

- Decisioner running with **25% canary** to a challenger bandit
  (softmax → ε-greedy → LinUCB)
- **24h of synthetic traffic** with simulated outcomes (so realized
  rewards exist)
- **Statistical lift report**: bootstrap 95% CI on
  `(challenger_profit / champion_profit - 1) × 100%`
- **Per-segment lift breakdown** (which segments the challenger wins on)
- A **decision**: ship-all / ship-some / rollback, grounded in numbers
- The whole thing **reproducible**: `make ab-test` re-runs end-to-end

**Items**:

| # | Item | Lands at |
|---|------|----------|
| 34 | NEW `outcome_simulator.py` — consumes `decisions` topic, uses customer ground-truth params to simulate realized response, emits to `outcomes` topic | `services/transactions/src/transactions/outcome_simulator.py` |
| 35 | Wire `outcome_collector` to join decisions + outcomes by decision_id in RW | `services/outcome_collector/src/outcome_collector/collector.py` (extend existing) |
| 36 | NEW RW MV `decision_outcomes` — joins decisions + outcomes; includes alias, segment, true_profit, predicted_uplift | `deployments/dev/risingwave/10_mv_decision_outcomes.sql` (NEW) |
| 37 | Unit tests for outcome_simulator | `services/transactions/tests/test_outcome_simulator.py` (NEW) |
| 38 | Train a challenger model (e.g., monotonic-GBM variant) via MLflow | `services/training_flow/src/training_flow/__main__.py --variant monotonic_gbm` (extend) |
| 39 | Set canary fraction to 25% via existing admin route | `curl POST /admin/canary -d '{"fraction": 0.25}'` |
| 40 | Run synthetic traffic at 200 RPS for 12h (modified k6 sustained) | `scripts/sustained_load.js` (NEW) |
| 41 | NEW notebook `notebooks/03_ab_test_analysis.ipynb` — pulls decision_outcomes MV, computes champion vs challenger profit by segment, bootstrap 1000x for 95% CI on lift, calibration plot | `notebooks/03_ab_test_analysis.ipynb` (NEW) |
| 42 | Welch's t-test + bootstrap CI; reject H0 if CI excludes 0 | analyzed in 03 notebook |
| 43 | NEW `docs/AB_TEST_REPORT_v2.0.2.md` — ship-all / ship-some / rollback decision with statistical justification | `docs/AB_TEST_REPORT_v2.0.2.md` (NEW) |
| 44 | Recorded 7-min screen capture | `docs/demos/ab_test_v2.0.2.mp4` |

**Effort**: ~3 sessions. **Cost**: ~$30 total (smaller cluster than load
test; 14h sustained). **Tag**: `v2.0.2` (A/B verified).

**Punchline pattern**:

> "Challenger model showed +X% profit lift (95% CI: +A% to +B%) on
> segment 0 — statistically significant. On segments 4 & 5
> (high-risk-new), lift was -Y% — challenger should NOT be promoted
> there. Decision: ship to segments 0-3 only via per-segment canary."

That answer demonstrates ML production discipline beyond "I trained a
model" — and is the exact reasoning pattern FAANG ML interviewers want
to hear.

---

### Phase J: Batch ingestion plane (PySpark on EMR / Glue) — added 2026-06-30

**Authored at v2.0.0 cluster validation session** after the user asked about
PySpark for pulling data from RDBMS at the ingestion stage. Real production
systems run a real-time + batch hybrid; we have the real-time plane (Kafka
→ RisingWave) but no batch plane yet. Phase J adds it.

**Architectural pattern**:

```
External RDBMS (credit bureau, customer master, RBI data)
        │
        ▼
PySpark on EMR Serverless / AWS Glue (triggered by Step Functions cron)
        │
        ▼
S3 parquet (raw → curated, partitioned by date)
        │
        ├──► Online feature store (DynamoDB or RisingWave batch-load)
        │
        └──► Training pipeline reads OFFLINE features for model fit
                ↓
        Feature schema versioned, point-in-time joins enforced

Real-time path stays unchanged: producer → Kafka → RW → MVs → decisioner
```

**Items** (a Phase J retrospective on top of the existing 21):

| # | Item | Lands at |
|---|------|----------|
| 22 | **EMR Serverless module + PySpark job template** | `infra/terraform/modules/emr_serverless/` + `services/batch_ingest/` |
| 23 | **Glue Data Catalog setup** | `infra/terraform/modules/glue/` |
| 24 | **JDBC ingestion job** (sample: pull from a fake "credit_bureau" Postgres into S3 parquet daily) | `services/batch_ingest/src/batch_ingest/jdbc_to_s3.py` |
| 25 | **Step Functions orchestrator** for cron-triggered batch jobs | `infra/terraform/modules/step_functions/` |
| 26 | **Offline feature loader in training_flow** — joins online (RW snapshot) + offline (S3 parquet) at training time with point-in-time correctness | `services/training_flow/src/training_flow/offline_feature_loader.py` |
| 27 | **Feature freshness metrics** (offline / online age + drift) — wired into existing bias/drift monitor | `services/drift_monitor/src/drift_monitor/freshness.py` |

**Why batch + streaming both, not one or the other**:

- RisingWave: 5-min / 1h / 24h windows — *online* features. Sub-second
  read at inference time.
- PySpark / S3: 90-day / 180-day / monthly snapshots — *offline*
  features. Cheaper at large scale, easier to backfill historical data,
  better fit for slow-changing dimensions (credit score, employment
  status, demographic data).
- A typical model uses BOTH at training time + online features only at
  inference. The "real-time decision" property is preserved because
  inference doesn't depend on offline features being fresh.

**Production cost expectation**:

- EMR Serverless: pay-per-job. A daily batch run scanning 10 GB of
  source data costs ~$0.20-0.50.
- Glue Catalog: free up to 1M tables.
- S3: ~$0.023/GB/month for standard tier.
- For our cohort_size = 1000 customers × 30 days backfill × 5 KB/event:
  ~150 MB of parquet, ~$0.01/month. Negligible.

**Tag mapping**: Phase J completes as v2.1.0 (after v2.0.0 ships). New
versioning: v2.0.0 = streaming complete; v2.1.0 = batch added.

---

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
