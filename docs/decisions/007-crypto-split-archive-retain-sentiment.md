# ADR 007: Crypto-domain code split — retain sentiment-scoring pattern; archive the rest

**Status:** Accepted
**Date:** 2026-06-04
**Decision makers:** Platform owner

## Context

This repository originated as a fork of a real-time-ML cohort course
focused on a crypto price-prediction pipeline. After Day 0 we had two
domains coexisting:

- **Crypto (inherited)**: `trades`, `candles`, `technical_indicators`,
  `predictor`, `prediction-api` — all crypto-market-specific
- **Finance (this project)**: the credit-decisioning platform whose
  architecture is documented in ADRs 001–006

The portfolio narrative is the **finance** system. The crypto code
served two purposes during Day 0:

1. A **substrate** for shared infrastructure (Kafka, RisingWave, MLflow,
   kind cluster setup, build / deploy scripts) that we genuinely reuse
2. A set of **reference patterns** for Quixstreams stream processing and
   Rust low-latency serving

Cost of keeping it:

- Lint surface is dominated by code we don't own (>300 ruff warnings,
  most in code we cannot defensibly refactor)
- `mypy --strict` cannot pass over code that wasn't written to be
  strictly typed
- The repo narrative is muddled — a reviewer arriving fresh sees two
  domains and asks "which one is yours?"
- Maintenance: every dependency upgrade affects code we won't ship

The patterns we genuinely reuse have already been documented in ADRs
001–006. We don't need the crypto code to defend the architecture —
the ADRs do that.

## Decision

We split the codebase into two repositories:

1. **`realtime-credit-decisioning/`** (this repo) — finance domain only,
   plus the minimal subset of crypto code we will actually reuse
2. **`realtime-ml-cohort-4-archive/`** (sibling directory) — frozen
   reference snapshot of the cohort-4 baseline, on a `cohort-4` branch
   at the pre-Day-0 commit (`5d09345`)

### What stays in this repo

| Item | Why kept |
|------|----------|
| `services/news/` | Produces the news event stream consumed by `news-sentiment` |
| `services/news-sentiment/` | The sentiment-scoring pipeline is reused as the `macro_sentiment_1h` feature in the finance domain (see `docs/02_data_and_features.md` and ADR 002) |
| `deployments/dev/kind/` | Shared cluster setup — Kafka, RisingWave, MLflow, Grafana — used by both domains |
| `deployments/dev/news-ingestor/` | K8s manifests for the news service |
| `Docker/news-ingestor.DockerFile` | Image build for news |
| `scripts/build-and-push-image.sh`, `scripts/deploy.sh` | General-purpose build pipeline |
| `dashboards/` (folder) | Empty after the crypto dashboard removal; filled with finance dashboards Day 7 |

### What was removed from this repo

| Item | Why removed |
|------|-------------|
| `services/{trades,candles,technical_indicators,predictor,prediction-api}/` | Crypto-market-specific; no code-level reuse in the finance domain |
| `ta-lib/` and `ta-lib-0.4.0-src.tar.gz` | Vendored C library used only by `technical_indicators` |
| `lessons/` | Course material |
| `dashboards/candles.json` | Crypto-specific Grafana dashboard |
| `deployments/dev/{trades,candles,technical-indicators,prediction-api,prediction-generator,training-pipeline,backfill-technical-indicators}/` | Manifests for removed services |
| `Docker/{trades,candles,technical_indicators,technical_indicators_1stage,prediction-api,prediction-generator,training-pipeline}.DockerFile` | Image builds for removed services |
| `mlruns/`, `state/` | Local MLflow store and Quixstreams state for crypto runs (gitignored anyway) |

## Consequences

### Positive

- **Clean portfolio narrative.** A reviewer landing on the repo sees a
  single finance domain story plus a small, justified retention of
  sentiment-scoring code
- **Mechanical lint / typecheck.** ruff and mypy --strict can pass over
  the entire active codebase without exceptions for "inherited code we
  don't own"
- **Cargo workspace is now decisioner-only.** No phantom `prediction-api`
  member; root `Cargo.toml` reflects what we actually build
- **Smaller surface for security review.** `detect-secrets` and
  dependency scanners run over less code
- **Faster CI** — fewer files to format, lint, type-check
- **Two-repo separation** matches the **production pattern of keeping a
  reference branch separate from the build branch** — a senior signal

### Negative

- **Cannot re-run the crypto pipeline from this repo.** If we wanted to
  demonstrate the original crypto predictor live, we would need to run
  it from the archive
- **The archive only contains commits up to `5d09345`** — work done
  across later cohort sessions that was never `git add`-ed is not in
  any branch. See incident in `docs/incidents.md`.
- **`news` + `news-sentiment` carry a domain mismatch** until refactored:
  the BAML schema emits sentiment scores keyed by `coin`, not by a
  generic asset/entity. We retain them under the agreement that Day 1
  (or whenever macro sentiment is wired into the finance pipeline) will
  refactor the schema. This is a known technical debt
- **No active maintenance of the archive.** It's a frozen reference. If
  upstream pushes new commits, we don't pull them automatically

## Alternatives considered

- **Keep crypto in this repo with lint exclusions** (the original
  Option A): rejected because (i) the portfolio narrative is muddled and
  (ii) carrying code you don't own forever costs more in confusion than
  it saves in deletion
- **Delete crypto entirely with no archive**: rejected because the
  session-1 seed commits are a useful "where this started" pointer for
  anyone investigating the project's provenance
- **Git submodule pointing at upstream cohort-4 repo**: rejected
  because the upstream URL was not present as a remote and we don't
  know it for certain. Could be re-evaluated if the upstream URL is
  confirmed
- **Move crypto code to a `legacy/` subdirectory in this repo**: rejected
  for the same reason as keeping with lint exclusions — visually present
  in the repo, muddles the narrative

## Related

- ADR 002 — RisingWave-as-feature-store. The `macro_sentiment_1h`
  feature defined there is the load-bearing reason `news-sentiment` is
  retained
- ADR 005 — MLflow with artifact proxy. The MLflow Deployment is part
  of the shared infrastructure retained in `deployments/dev/kind/`
- ADR 006 — Kustomize base + overlays. The shared cluster setup
  retained in `deployments/dev/kind/` is the substrate the new overlays
  build on
- `docs/incidents.md` — the honest record of the uncommitted-work loss
- `docs/repo_layout.md` — updated services/ inventory after the split
