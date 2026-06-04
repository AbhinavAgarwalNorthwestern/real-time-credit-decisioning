# Model Card

Follows the structure of [Mitchell et al. 2019, *Model Cards for Model
Reporting*](https://arxiv.org/abs/1810.03677).

One card per registered model. Day 0 has the **template only**;
per-model card entries populate as Day 2 trains the first model.

---

## Model: `cli_uplift_<segment>_v<N>`

(Replace `<segment>` with the segment name; one card per segment.)

### Model details

- **Owner**: this project
- **Date**: _(populated when the first version is registered Day 2)_
- **Version**: _(MLflow registry version number)_
- **Architecture**: T-learner uplift model, per-arm neural net,
  exported to ONNX
- **Framework**: PyTorch 2.4+ (training); ONNX Runtime via `ort` 2.0
  (serving)
- **License**: portfolio use

### Intended use

- **Primary**: per-event credit-limit-increase decisioning for the
  named segment
- **Secondary**: feeds the bandit's per-arm uplift estimate

### Out-of-scope use

- Credit underwriting (initial limit setting at application time)
- Credit-decline decisions (this system only chooses among CLI / fraud
  check / nothing)
- Cross-segment use — each model is trained on data from one segment
  and is not transferable

### Factors

- **Segment**: low/med/high risk × new/tenured customer (six segments)
- **Time window**: training-data horizon, default 90 days; specified
  per training run

### Metrics (populated Day 2)

- **Uplift AUC** on held-out test set: _(TBD)_
- **Calibration (Brier score)**: _(TBD)_
- **Per-arm prediction distribution**: KL divergence vs champion: _(TBD)_

### Training data

- See `docs/data_card.md`
- Training window: _(TBD)_
- Number of decisions in training set: _(TBD)_
- Class balance: _(TBD — usually skewed toward `NOTHING` action)_

### Evaluation data

- Held-out test set: same window, last 10% by event time
- Off-policy evaluation: applies on production decision log via
  IPS / SNIPS / DR (Day 6)

### Ethical considerations

- ECOA-protected attributes excluded from feature set (enforced in
  `behavioral_features` service feature allowlist)
- Adverse-action notification driven by SHAP delta logged at decision
  time; reason codes generated via lookup table (Day 8)
- 4-eyes promotion gate on champion-challenger swaps

### Caveats and recommendations

- Synthetic training data is not a substitute for production data; this
  model is not deployed against real customers
- Drift detector (`services/drift_monitor`) is the operational
  safety net for distribution shift
- Auto-rollback if production performance regresses post-promotion

### Lineage

- Code commit: _(TBD — captured in MLflow run params)_
- Training docker image digest: _(TBD)_
- Feature view version: _(TBD)_

---

## Status

Template stable through Day 0. Day 2 produces the first six segment
model cards from a Jinja template populated by the Metaflow training
flow.
