# Session Prompt — `realtime-credit-decisioning`

**Use this to start a fresh Claude session for this project.** Copy the
block below verbatim into a new conversation as your first message.

---

## Copy-paste prompt (for the next session, 2026-06-30+)

```
I'm continuing work on the realtime-credit-decisioning project located at
C:\Users\abhin\realtime-credit-decisioning\ (inside a VS Code devcontainer
mounted at /workspaces/realtime-credit-decisioning).

I am running this session OUTSIDE the devcontainer (VS Code on Windows).
I will paste commands you give me into the devcontainer terminal and
report results. Don't try to run kubectl/psql/python directly.

Before doing ANYTHING else, please read these files in order to restore
context:

  1. docs/STATUS.md                  — current phase, what's done, what's
                                       next, the v1.1.0 cluster-apply command
                                       ready to paste
  2. docs/scope_expansion_plan.md    — the 21-item roadmap (Phases A-I);
                                       AUTHORITATIVE for everything after v1.1.0
  3. docs/INFRASTRUCTURE.md          — cluster architecture + fix-log
                                       (especially items on Hummock errors,
                                       MinIO 429 cap, RW pod restart recipe,
                                       'latest' mode rationale)
  4. docs/repo_layout.md             — directory conventions + invariants
  5. docs/dgp_design.md              — full DGP spec
  6. .claude/plans/sleepy-questing-sundae.md  — customer-attributes plan
                                       (Phase A of scope_expansion_plan.md)

You should also see project context from the memory system at
C:\Users\abhin\.claude\projects\C--Users-abhin\memory\MEMORY.md.

Current state as of 2026-06-30 end-of-session:
  - Day 1-7 + FAANG hardening: COMPLETE (v1.0.0 baseline)
  - Customer attributes Steps 1-9: COMPLETE
  - 17 of 21 scope-expansion items: SHIPPED at code level (~30 files,
    ~210 tests, ~16 libraries) — see STATUS.md table for the full list
  - v1.1.0 cluster apply: PENDING (single chained command in STATUS.md
    "Task 1 — v1.1.0 cluster apply")
  - Step 11b notebook update: BLOCKED on v1.1.0 model artifact existing
  - Wire post_training_eval into __main__.py: deferred for user inspection

What I'd like you to do:

1. Tell me what state the project is in (after reading STATUS.md), what
   shipped in the prior session, and what's pending.
2. Confirm the v1.1.0 cluster-apply command from STATUS.md is the right
   next move, OR propose an alternative if you see a better path.
3. WAIT for me to confirm before pasting any cluster commands or making
   code edits.

Hard lines I want you to hold:
  - No "Co-Authored-By: Claude" on commits
  - No fabricating employer attribution (Capgemini, Synchrony, etc.)
  - Plan before code: show the 2-level plan (whole project + this task)
    before tool calls, wait for sign-off
  - At every natural checkpoint provide demo command + test command, wait
    for me to verify before continuing
  - Point-in-time correctness is non-negotiable
  - Single master_seed → derive_seed(master_seed, namespace) for ALL RNGs
  - Decisioner uses loguru, NOT structlog
  - Bandit is softmax IN PRODUCTION (deep-RL alternatives in
    services/decisioner/src/decisioner/bandit_ladder.py are research
    artifacts, NOT the deployed policy)
  - asyncpg needs Connection.reset noop for RisingWave (Day-3 fix-log)
  - 02_mv_events_enriched.sql has GAP-GUARD CLAMPS shipped in v1.1.0:
    time_since_last_s capped at 3600s + is_paydown gated on ≤ 3600s gap.
    DO NOT REMOVE these clamps — they handle producer-restart discontinuities.
  - DGP rate_heterogeneity threshold is 0.7 in dev (was 1.0); documented
    rationale in validate_dgp.py. AWS overlay restores 1.0.
  - CLOUD-AGNOSTIC: base manifests stay portable; AWS-specific code only
    in deployments/overlays/aws-eks/ + infra/lib/ + infra/terraform/.
    See feedback_cloud_agnostic memory.
  - 00_source_transactions.sql uses scan.startup.mode='latest' (NOT
    'earliest') to avoid the Hummock "decreasing now" bug during
    snapshot backfill replay. Multi-line --  comments go OUTSIDE the
    WITH(...) clause.
  - Producer state checkpoints to MinIO (state_checkpoint.py) — backfill
    Job writes on exit, live pod loads on start. Preserves customer
    balances across pod restart.

Tell me what state we're in, then wait for me to confirm before
proceeding to v1.1.0 cluster apply.
```

---

## Why this prompt works

It does six things a fresh-session assistant needs:

1. **Points at the project** — absolute path + the devcontainer mount path
2. **Sets execution mode** — I'm outside the container, I paste commands you give me
3. **Restores context** — the 6 files cover state, infra, layout, DGP, scope, plan
4. **Anchors to the immediate state** — what shipped, what's pending, exact next command
5. **States hard lines** — the things I've already explained at length and don't want re-derived
6. **Demands a confirmation cycle** — assistant has to summarize what state we're in and what to do, BEFORE touching code/cluster

## What's up-to-date in this prompt (2026-06-30)

| Hard line | Source |
|-----------|--------|
| Gap-guard clamps on events_enriched | Added v1.1.0 — `02_mv_events_enriched.sql` (`time_since_last_s ≤ 3600` + `is_paydown` gated on `≤ 3600s` gap) |
| DGP threshold 0.7 in dev / 1.0 in AWS | `validate_dgp.py` with documented rationale |
| `scan.startup.mode='latest'` | `00_source_transactions.sql` — avoids Hummock "decreasing now" |
| Producer state checkpointing | `state_checkpoint.py` + `generator.py` + `main.py` SIGTERM-handler + K8s manifest secret injection |
| Bandit ladder is research-only | `decisioner/src/decisioner/bandit_ladder.py` — 4 methods (ε-greedy / softmax / LinUCB / Thompson) |
| Post-training eval orchestrator | `training_flow/src/training_flow/post_training_eval.py` — wires all credit-stats libs; not yet called from `__main__.py` |

## Files / libraries the next session should be aware of

**Shipped this session (parallel build-out, 2026-06-29/30):**

Credit-stats Tier S (Phase B):
- `services/training_flow/src/training_flow/woe_scorecard.py` (S6)
- `services/training_flow/src/training_flow/calibration.py` (S1)
- `services/training_flow/src/training_flow/discrimination.py` (S3 — Gini/KS/Lorenz lives here)
- `services/training_flow/src/training_flow/vintage.py` (S2)
- `services/training_flow/src/training_flow/loss_forecasting.py` (S4)
- `services/training_flow/src/training_flow/monotonic_gbm.py` (S5)

Credit-stats Tier A (Phase D):
- `services/training_flow/src/training_flow/survival.py` (A7)
- `services/training_flow/src/training_flow/reject_inference.py` (A8)
- `services/training_flow/src/training_flow/stress_test.py` (A9)

Credit-stats Tier B (Phase H):
- `services/training_flow/src/training_flow/bayesian_pd.py` (B10 — PyMC + numpy fallback)
- `services/drift_monitor/src/drift_monitor/csi.py` (B12)

FAANG items:
- `services/bias_monitor/` (1A — full new service: main.py + Dockerfile + 4 K8s manifests)
- `services/decisioner/src/decisioner/bandit_ladder.py` (1B)
- `services/training_flow/src/training_flow/model_card.py` (1C)
- `deployments/base/external-secrets/external-secrets-operator.yaml` (1D base)
- `deployments/overlays/aws-eks/external-secrets-store.yaml` (1D AWS)
- `infra/terraform/modules/secrets_rotation/main.tf` (1D Terraform)
- `deployments/overlays/aws-eks/kafka-prod-rf3.yaml` (2A)
- `infra/terraform/modules/tagging/main.tf` (2B) + `docs/FINOPS.md`
- `services/training_flow/src/training_flow/backfill_feature.py` (2C — versioned FeatureRegistry, two-topic dispatch)

Integration:
- `services/training_flow/src/training_flow/post_training_eval.py` — `run_full_eval()` + `log_to_mlflow()`

Producer hardening:
- `services/transactions/src/transactions/state_checkpoint.py` (S3-compatible save/load)
- `services/transactions/src/transactions/generator.py` (`restore_from` param)
- `services/transactions/src/transactions/main.py` (load on start + periodic save + finally-block save)
- `services/transactions/src/transactions/config.py` (state_* env vars)
- `deployments/base/services-finance/transactions/deployment.yaml` (MinIO secret env vars)
- `deployments/base/services-finance/transactions/backfill-job.yaml` (same secret injection)
- `services/transactions/pyproject.toml` (+ boto3 + moto[s3])
- `services/transactions/tests/test_state_checkpoint.py` (9 tests with moto S3 mock)

DDL updates (for v1.1.0):
- `deployments/dev/risingwave/00_source_transactions.sql` — scan.startup.mode='latest' + 5 customer-attribute columns
- `deployments/dev/risingwave/02_mv_events_enriched.sql` — gap-guard clamps on `time_since_last_s` and `is_paydown`
- NEW `deployments/dev/risingwave/08_mv_customer_attributes.sql` — per-customer MAX aggregation
- RENAMED `07_mv_behavioral_features_serving.sql` → `09_mv_behavioral_features_serving.sql` (lex-order required after 08_)
- `services/training_flow/src/training_flow/validate_dgp.py` — threshold 1.0 → 0.7

Test files (~210 new tests):
- `services/transactions/tests/test_customer.py` (Phase A Step 10)
- `services/transactions/tests/test_generator.py` (Phase A Step 10)
- `services/transactions/tests/test_state_checkpoint.py` (state-checkpoint)
- `services/decisioner/tests/test_feature_lookup.py` (Phase A Step 10)
- `services/decisioner/tests/test_bandit_ladder.py` (1B)
- `services/training_flow/tests/test_mv_reader.py` (Phase A Step 10)
- `services/training_flow/tests/test_woe_scorecard.py`
- `services/training_flow/tests/test_calibration.py`
- `services/training_flow/tests/test_discrimination.py`
- `services/training_flow/tests/test_vintage.py`
- `services/training_flow/tests/test_loss_forecasting.py`
- `services/training_flow/tests/test_monotonic_gbm.py`
- `services/training_flow/tests/test_survival.py`
- `services/training_flow/tests/test_reject_inference.py`
- `services/training_flow/tests/test_stress_test.py`
- `services/training_flow/tests/test_model_card.py`
- `services/training_flow/tests/test_bayesian_pd.py`
- `services/training_flow/tests/test_backfill_feature.py`
- `services/training_flow/tests/test_post_training_eval.py`
- `services/bias_monitor/tests/test_fairness.py`
- `services/drift_monitor/tests/test_csi.py`

Notebooks:
- NEW `notebooks/01_customer_eda.ipynb` — pre-model EDA, 14 cells + §8 WoE/IV (Step 11a + Phase B S6)
- `notebooks/ml_evaluation.ipynb` — Section 12 update PENDING (Step 11b — next session)

Docs:
- `docs/scope_expansion_plan.md` — 21-item roadmap (authoritative)
- `docs/FINOPS.md` — cost-attribution tagging schema
- `docs/INFRASTRUCTURE.md` — fix-log items for the new issues encountered

## Variants for specific tasks

**Fresh session intended to ship v1.1.0 immediately (most likely use case for next session):**

```
I want to ship v1.1.0 in this session. Read docs/STATUS.md and confirm the
Task 1 "v1.1.0 cluster apply" command is correct. Then paste it for me, I'll
run it. Expect ~25-30 min of cluster work. Report progress at each step.

After v1.1.0 tags successfully, proceed to Task 2 (Step 11b notebook update
using the post_training_eval library) and Task 3 (wire post_training_eval
into training_flow/__main__.py).
```

**For a debugging session if v1.1.0 fails:**

```
The v1.1.0 cluster apply from STATUS.md Task 1 failed at step [N] with
error [X]. Don't write code yet. Walk me through:
  1. What the symptom means (check docs/INFRASTRUCTURE.md §7 fix-log for known issues)
  2. The minimum-blast-radius fix
  3. Whether to retry the chained command or split into smaller pieces
```

**For Phase J (next phase after v1.1.0 ships):**

```
v1.1.0 is tagged. Now I want to extend [pick one]:
  - Step 11b notebook (post-model evaluation cells in ml_evaluation.ipynb §12)
  - Wire post_training_eval into training_flow/__main__.py
  - Document a new ADR for the design decisions made in this scope expansion
  - Start a new project from the master prompt regime (project_master_prompt memory)
```

## Memory system

The persistent memory directory `C:\Users\abhin\.claude\projects\C--Users-abhin\memory\`
contains preferences saved across all sessions. `MEMORY.md` is auto-loaded
at session start. Notable entries:

- `user_role.md` — 11+ yrs DS, dual-positioning FAANG/non-FAANG
- `feedback_code_hygiene.md` — uv only, mypy --strict, ruff line 100
- `feedback_guided_teaching.md` — guide fully, explain why
- `feedback_plan_before_code.md` — show 2-level plan, wait for sign-off
- `feedback_demos_tests_checkpoints.md` — describe + demo + test + checkpoint
- `feedback_cloud_agnostic.md` — base portable, AWS-specific only in overlays/
- `project_master_prompt.md` — 30-day plan (this is project #1)
- `project_azercell_forecasting.md` — separate parallel project
- `project_credit_decisioning.md` — pointer to docs/STATUS.md + scope_expansion_plan.md
- `reference_ml_lifecycle_template.md` — 7-flow pattern reused across projects
