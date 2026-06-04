# 04 — Results and Metrics

This chapter is intentionally **mostly empty at Day 0**. It gets
populated as each day's work produces measurements. The structure is
fixed; the numbers come later.

## Headline metrics (target → measured)

| Metric | Target | Measured | When |
|--------|--------|----------|------|
| `/decide` p99 latency | < 50 ms | _(Day 7)_ | k6 load test |
| `/decide` throughput (per replica) | ≥ 5 000 / s | _(Day 7)_ | k6 load test |
| Uplift estimate AUC (per segment) | ≥ 0.70 | _(Day 2)_ | Offline validation |
| Bandit cumulative regret | TBD | _(Day 3)_ | Synthetic simulation |
| Off-policy evaluation: challenger IPS vs champion | > 0% lift in canary | _(Day 6)_ | OPE harness |
| Drift detection true-positive rate | ≥ 90 % | _(Day 5)_ | Drift injection test |
| Retraining wall-clock (full fan-out) | < 30 min | _(Day 5)_ | Metaflow run |
| End-to-end demo time | < 10 min | _(Day 7)_ | Tour rehearsal |

## Per-segment uplift quality (Day 2 deliverable)

| Segment | Cohort size | Uplift AUC | Calibration (Brier) | Notes |
|---------|-------------|------------|---------------------|-------|
| `low_risk_tenured` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |
| `med_risk_tenured` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |
| `high_risk_tenured` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |
| `low_risk_new` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |
| `med_risk_new` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |
| `high_risk_new` | _(TBD)_ | _(TBD)_ | _(TBD)_ | |

## Latency budget realization (Day 7 deliverable)

| Step | Budget | Measured p50 | Measured p99 |
|------|--------|--------------|--------------|
| Feature lookup (RisingWave) | 5 ms | _(TBD)_ | _(TBD)_ |
| Segment routing | < 1 ms | _(TBD)_ | _(TBD)_ |
| ONNX inference (per segment) | 5–10 ms | _(TBD)_ | _(TBD)_ |
| Bandit selection | < 1 ms | _(TBD)_ | _(TBD)_ |
| Audit log enqueue | < 1 ms | _(TBD)_ | _(TBD)_ |
| HTTP response | 2 ms | _(TBD)_ | _(TBD)_ |
| **Total** | _< 50 ms p99_ | _(TBD)_ | _(TBD)_ |

Cross-reference: ADR 004 has the projected budget table; this chapter
holds the measured numbers.

## Cost (Day 8 deliverable)

| Item | Cost |
|------|------|
| EKS control plane | $73/mo |
| Node group (m6i.large × 3) | _(TBD)_ |
| ECR storage | _(TBD)_ |
| S3 storage (MLflow + decision log) | _(TBD)_ |
| Estimated $/decision at 5k/s sustained | _(TBD)_ |

## Failure-mode catalog

Discovered failure modes get filed in `docs/incidents.md`; the count
ends up here.

| Day | Incidents | Severity |
|-----|-----------|----------|
| 0 | 1 — MinIO credentials hardcoded in 3 places (resolved via rotation + secretKeyRef) | Low (private repo) |

## Status

Skeleton populates over Days 1–8. This chapter never gets written
preemptively — only measured numbers go here.
