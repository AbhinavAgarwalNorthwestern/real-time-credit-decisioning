# ADR 011: Drop news + news-sentiment retention (supersedes ADR 007)

**Status:** Accepted
**Date:** 2026-06-07
**Decision makers:** Platform owner
**Supersedes:** [ADR 007](007-crypto-split-archive-retain-sentiment.md)

## Context

ADR 007 retained `services/news` and `services/news-sentiment` from Pau's
upstream cohort. The justification was that the `macro_sentiment_1h`
feature documented in `docs/02_data_and_features.md` would be fed by the
news-sentiment topic — a small re-use of an existing streaming
pattern to add a macro-context input to the credit-decisioning model.

By Day-2 D2-1b, the actual feature set the per-segment T-learners consume
is fully behavioral (per `02_mv_events_enriched.sql` and the five per-window
MVs `behavioral_features_{5m,1h,24h,7d,30d}`). `macro_sentiment_1h` was
never wired into the training pipeline or the decisioner. The
news/news-sentiment services were running cost (cluster pods, image builds,
mypy + ruff overhead, workspace member resolution) for a signal nothing
actually consumes.

Two paths forward:

- **Option A — Wire macro_sentiment_1h in for real.** Add a RisingWave
  source on the `news_sentiment` topic, a JOIN MV against the behavioral
  features, a new column on the serving MV. Then re-train. Real work,
  no clean payoff: the credit-decisioning signal is already in the
  behavioral features; macro-sentiment moves the needle marginally if at
  all on a synthetic DGP that doesn't model macro shocks.
- **Option B — Drop the retention.** Treat news + news-sentiment as
  Pau's-course archive: directories remain on disk for provenance, but
  they exit the uv workspace, exit the deployment manifests, exit the
  docs as "we use them," and the `macro_sentiment_1h` feature disappears
  from the platform description.

## Decision

We adopt **Option B**: drop news and news-sentiment from the active
project surface.

Concrete changes:

- `pyproject.toml` workspace members no longer list `services/news` or
  `services/news-sentiment`.
- `docs/02_data_and_features.md` "Macro/sentiment feature view" section
  removed; the platform's feature set is now exclusively behavioral.
- `docs/repo_layout.md` `services/` table updated to mark both as
  archive (not "RETAINED").
- `docs/dgp_design.md` (if it references macro_sentiment) — feature
  removed from the catalogue.
- The directories themselves stay on disk so the Pau-course provenance
  remains auditable. They are no longer touched by lint, type-check, or
  CI workflows.

## Consequences

### Positive

- **Smaller cognitive surface.** Reviewers and interview audiences see
  exactly the components the credit-decisioning system uses; nothing
  that "we kept around in case." Senior signal is tighter.
- **Faster CI and devcontainer setup.** uv sync, mypy, ruff stop
  walking news/news-sentiment trees.
- **Honest documentation.** `02_data_and_features.md` no longer claims
  `macro_sentiment_1h` is a system feature when it isn't.
- **Frees a future ADR.** If macro signals genuinely earn their keep
  later (e.g., the model card asks for an interest-rate-environment
  control), reintroducing them becomes a deliberate, scoped decision
  with its own ADR rather than carried baggage.

### Negative

- **Loses the easiest cross-domain narrative.** ADR 007 framed retention
  as "reuse a Quixstreams + RW stateful pattern across domains."
  Removing it means the project demonstrates only the finance-domain
  Quixstreams app (the transactions producer). For a portfolio
  reviewer who specifically wants to see multi-domain pipeline reuse,
  this is a narrower story.
  *Mitigation*: the transactions producer alone is a complete
  Quixstreams demonstration. Day-5 retraining-flow and Day-3 decisioner
  add other systems-design surface (Metaflow, FastAPI) without needing
  a second Quixstreams service.
- **The DGP design loses a "macro context" mechanic.** If we ever want
  the bandit to learn "stay conservative when rate news is bearish," we
  no longer have a sentiment stream to feed it.
  *Mitigation*: the synthetic DGP doesn't model macro shocks anyway, so
  this is theoretical, not realized.

## Alternatives considered

- **Option A — wire macro_sentiment_1h in for real**. Rejected: marginal
  signal benefit against real implementation cost.
- **Delete the directories entirely.** Considered, rejected: the Pau-
  course archive has provenance value (showing the inheritance baseline
  and how much was rebuilt). Leaving the dirs on disk costs nothing once
  they exit the workspace.

## Related

- ADR 007 — superseded by this ADR (the retention decision is reversed)
- `docs/02_data_and_features.md` — macro/sentiment section removed
- `docs/repo_layout.md` — services table updated
- `services/news/`, `services/news-sentiment/` — directories remain as
  archive; not on the active project surface
