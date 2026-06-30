# Regulatory Compliance — Realtime Credit Decisioning Platform

## Applicable Regulations

| Regulation | Jurisdiction | Relevance |
|-----------|-------------|-----------|
| ECOA (Reg B) | US Federal | Adverse-action notice required for credit denials |
| FCRA | US Federal | Fair credit reporting; model cannot use prohibited bases |
| SR 11-7 | US Federal (OCC/Fed) | Model risk management for banking organizations |
| GDPR Art. 22 | EU | Right to explanation for automated decisions |
| CCPA | California | Consumer data access and deletion rights |

## Adverse-Action Reason Codes (ECOA / Reg B)

When the decisioner denies a customer (action = NOTHING), the platform
generates specific, actionable reason codes explaining the factors that
most negatively influenced the decision.

**Implementation:**

1. **Real-time (in-request):** Marginal SHAP approximation in `adverse_action.py`
   computes per-feature contributions against a population baseline.
   Top-4 negative factors are returned in the API response.

2. **Batch (compliance-grade):** The `shap_consumer` service reads denied
   decisions from Kafka, runs full KernelSHAP with 100 background samples,
   and stores reason codes with 7-year retention (S3 Glacier lifecycle).

**Reason code registry:** Each feature maps to a human-readable reason
(R001–R022) reviewed by compliance. See `shap_consumer/main.py:REASON_CODE_MAP`.

## Model Risk Management (SR 11-7)

### Model Inventory

| Model | Type | Purpose | Owner | Validation Cadence |
|-------|------|---------|-------|--------------------|
| uplift_per_segment | T-learner (neural) | Credit limit increase uplift | ML Engineering | Quarterly |
| Contextual bandit | Softmax over E[profit] | Action selection | ML Engineering | Quarterly |

### Three Lines of Defense

1. **First line (model development):**
   - DGP validation gate (rate heterogeneity, segment separability, temporal signal)
   - Group-constrained train/val split (no customer leakage)
   - OOT holdout (15% customers, never seen during training)
   - OPE (IPS, SNIPS, Doubly Robust with bootstrap CI)

2. **Second line (model validation):**
   - Champion/challenger shadow scoring with canary routing
   - Auto-rollback watchdog (p99 latency, profit drop, error rate)
   - 4-eyes promotion gate (two independent approvals required)
   - 7 drift detectors (PSI, KS, JS divergence, ADWIN, performance gap, schema, per-segment)

3. **Third line (internal audit):**
   - Complete audit trail: every decision logged to Kafka → S3 with 7-year retention
   - Feature vector hash for reproducibility
   - Model version tracking in MLflow
   - SHAP explanations stored alongside decisions

### Model Documentation Package

Each model version in MLflow includes:
- Training data statistics (row counts, feature distributions)
- DGP gate results (pass/fail per check)
- Baseline comparison reports
- OPE leaderboard (IPS, SNIPS, DR estimates with 95% CI)
- ONNX export equivalence check
- Feature schema (column names, order, expected ranges)

## Data Governance

### Protected Classes

The model does NOT use any prohibited bases as features:
- Race, color, national origin
- Religion
- Sex, marital status
- Age (except as permitted by ECOA)
- Receipt of public assistance

Feature list is limited to behavioral transaction patterns (velocity,
spend, utilization, payment behavior, merchant diversity).

### Data Retention

| Data Type | Retention | Storage | Encryption |
|-----------|-----------|---------|------------|
| Decision audit logs | 7 years | S3 (IA → Glacier) | AES-256 (SSE-KMS) |
| Adverse-action reasons | 7 years | S3 (IA → Glacier) | AES-256 (SSE-KMS) |
| Feature vectors | 7 years | S3 (IA → Glacier) | AES-256 (SSE-KMS) |
| Training data | 3 years | S3 | AES-256 (SSE-KMS) |
| Model artifacts | Indefinite | MLflow + S3 | AES-256 (SSE-KMS) |

### Access Controls

- **Feature store (RisingWave/SageMaker):** RBAC via K8s ServiceAccounts + IRSA
- **S3 buckets:** IAM policies scoped per service (least privilege)
- **MLflow:** In-cluster, no public endpoint; accessed via K8s NetworkPolicy
- **Kafka topics:** ACLs per consumer group

## Monitoring & Alerting

| Signal | Threshold | Action |
|--------|-----------|--------|
| p99 latency | > 50ms | Page on-call |
| Error rate | > 0.1% | Page on-call |
| Profit drop (canary vs baseline) | > 10% | Auto-rollback challenger |
| Drift detected (any detector) | Signal fires | Promote hot-challenger |
| Denial rate spike | > 2x baseline | Alert compliance team |
| Model staleness | > 6 hours since last candidate | Alert ML engineering |

## Audit Trail

Every `/decide` call produces an immutable audit record containing:
- `decision_id` (UUID)
- `customer_id`
- `decision_ts_ms`
- Champion and challenger model URIs and versions
- Both arms' actions and propensities
- Which arm acted (`acted_on_alias`)
- Feature vector hash (blake2b) for reproducibility
- SHAP-based adverse-action reasons (for denials)
- Regulatory flags (`adverse_action_eligible`)

Records flow: FastAPI → Kafka `decisions` topic → RisingWave `decision_log` MV → S3 archival.
