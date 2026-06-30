# training_flow

Day-2 offline training pipeline. Day-5 `retraining_flow` wraps the same
modules in a Metaflow `@kubernetes` flow.

## Pipeline (per `docs/STATUS.md` § Day-2 plan)

1. **Trigger backfill** — apply a K8s Job that runs the existing
   `transactions` image with `TXN_MODE=backfill` and a configured window.
   `backfill_trigger.py` waits for completion.
2. **Wait for RisingWave to settle** — `mv_reader.poll_until_stable()`
   polls `behavioral_features_5m` until row count stops growing for the
   configured stability window.
3. **Query per-window MVs** — `mv_reader.query_window_features()` loads
   each MV (`5m, 1h, 24h, 7d, 30d`) into a pandas DataFrame.
4. **Join feature snapshots** — bucket each long-horizon row's
   `window_end` onto the 5m grid; merge in pandas. Each resulting row is
   one training example with features at every horizon as of that 5m bucket.
5. **Synthetic-RCT labels** — `label_simulator.assign_labels()` adds
   `T ~ Bernoulli(0.5)` per ADR 010 and simulates `(accepted, spend_delta,
   defaulted, profit)` from the customer's embedded true response
   parameters and the row's context (utilization, velocity_24h,
   paydown_rate_30d).
6. **Write parquet** — `data/training.parquet` (local first). Promote to
   MinIO via `infra/lib/object_store.py` once stable.

## Design pointers

- ADR 002 — RisingWave as feature store (the MVs we query)
- ADR 009 — Pure RisingWave SQL for feature computation
- ADR 010 — Synthetic RCT for treatment assignment

## Quick start (devcontainer)

```bash
# Requires the kind cluster running + the 9 MVs from D2-1a present.
uv sync --all-extras
uv run python -m training_flow --backfill-days 7 --output data/training.parquet
```

Tests:

```bash
uv run pytest services/training_flow/tests/ -v
```
