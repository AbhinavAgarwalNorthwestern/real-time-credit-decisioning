"""Auto-generated model card + SR-11-7 sign-off checklist — FAANG Tier 1C.

For every MLflow model version registered by the training pipeline, generate
a model card (Mitchell et al. 2019 schema) summarizing:

- Intended use + out-of-scope use
- Performance: Kendall τ, Gini, KS, calibration ECE, per-segment metrics
- Fairness: demographic parity / equalized odds (from bias_monitor library)
- Stress test results: portfolio EL under baseline / adverse / severely adverse
- Monotonicity attestation (which features have which constraints)
- Training data lineage: parquet hash, MLflow run ID, master seed
- Decision threshold + adverse-action reason codes
- SR-11-7 review checklist with sign-off fields

The model card is logged as an MLflow artifact next to the model, accessible
via MLflow UI + the registry API. The SR-11-7 checklist is a separate
markdown the model risk team fills in before promotion to champion.

Cloud-agnostic: pure Python + jinja2-style string templating. No MLflow
client dependency in this module — caller passes results in. (Wiring to
the pipeline happens in mlflow_log.py.)

References:
- Mitchell et al. (2019), "Model Cards for Model Reporting"
- FRB SR 11-7, "Guidance on Model Risk Management"
- Google Model Card Toolkit (public reference implementation)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Aggregate model performance for the card."""

    kendall_tau: float | None = None
    gini: float | None = None
    ks_statistic: float | None = None
    auc: float | None = None
    brier_score: float | None = None
    expected_calibration_error: float | None = None
    hl_p_value: float | None = None


@dataclass(frozen=True, slots=True)
class FairnessMetrics:
    """Aggregate fairness assessment per dimension."""

    dimension: str  # 'segment_id' or 'credit_score_bucket'
    demographic_parity_ratio: float
    demographic_parity_pass: bool
    equalized_odds_tpr_gap: float | None = None
    equalized_odds_fpr_gap: float | None = None
    equalized_odds_pass: bool = True
    predictive_parity_ppv_gap: float | None = None
    predictive_parity_pass: bool = True
    violations: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StressTestSummary:
    """One row of the stress-test summary."""

    scenario_name: str
    expected_loss: float
    loss_rate_pct: float
    weighted_avg_pd: float


@dataclass(frozen=True, slots=True)
class ModelCardInputs:
    """Everything needed to render a model card."""

    model_name: str
    model_version: str
    mlflow_run_id: str
    master_seed: int
    training_parquet_sha256: str
    n_training_rows: int
    n_features: int
    feature_cols: tuple[str, ...]
    champion_alias: str
    performance: PerformanceMetrics
    fairness: list[FairnessMetrics] = field(default_factory=list)
    stress_test: list[StressTestSummary] = field(default_factory=list)
    monotonicity_constraints: dict[str, str] = field(default_factory=dict)
    adverse_action_reason_codes: dict[str, str] = field(default_factory=dict)
    intended_use: str = (
        'Real-time credit decisioning: predict probability of default + '
        'expected uplift from a credit-limit increase offer. Used by the '
        'decisioner service for production POST /decide endpoints.'
    )
    out_of_scope_use: str = (
        'NOT for: cross-sell decisions, fraud detection (use the fraud-check '
        'route), pricing of new originations, lifetime value estimation.'
    )
    horizon_days: int = 365  # 12-month observation horizon for default


def render_model_card_markdown(inputs: ModelCardInputs) -> str:
    """Render a Mitchell-et-al-style model card as markdown.

    Output is suitable for direct upload to MLflow as an artifact.
    """
    now = datetime.now(tz=timezone.utc).isoformat()
    perf = inputs.performance

    lines = [
        f'# Model Card — {inputs.model_name} v{inputs.model_version}',
        '',
        f'**Generated:** {now}',
        f'**MLflow run ID:** `{inputs.mlflow_run_id}`',
        f'**Champion alias:** `{inputs.champion_alias}`',
        '',
        '## Model Details',
        f'- **Name:** {inputs.model_name}',
        f'- **Version:** {inputs.model_version}',
        f'- **Master seed:** {inputs.master_seed} (reproducible via `derive_seed(master_seed, namespace)`)',
        f'- **Training data SHA256:** `{inputs.training_parquet_sha256}`',
        f'- **Training rows:** {inputs.n_training_rows:,}',
        f'- **Features:** {inputs.n_features} ({", ".join(inputs.feature_cols[:5])}…)',
        f'- **Default horizon:** {inputs.horizon_days} days',
        '',
        '## Intended Use',
        inputs.intended_use,
        '',
        '## Out-of-Scope Use',
        inputs.out_of_scope_use,
        '',
        '## Performance',
        f'- **Kendall τ** (uplift rank correlation vs ground truth): `{perf.kendall_tau:.4f}`'
        if perf.kendall_tau is not None
        else '- Kendall τ: not measured',
        f'- **Gini coefficient:** `{perf.gini:.4f}`'
        if perf.gini is not None
        else '- Gini: not measured',
        f'- **KS statistic:** `{perf.ks_statistic:.4f}`'
        if perf.ks_statistic is not None
        else '- KS: not measured',
        f'- **AUC:** `{perf.auc:.4f}`'
        if perf.auc is not None
        else '- AUC: not measured',
        f'- **Brier score:** `{perf.brier_score:.4f}`'
        if perf.brier_score is not None
        else '- Brier: not measured',
        f'- **Expected Calibration Error:** `{perf.expected_calibration_error:.4f}`'
        if perf.expected_calibration_error is not None
        else '- ECE: not measured',
        f'- **Hosmer-Lemeshow p-value:** `{perf.hl_p_value:.4f}` (>0.05 means well-calibrated)'
        if perf.hl_p_value is not None
        else '- HL: not measured',
        '',
        '## Fairness Assessment',
    ]

    if inputs.fairness:
        for f in inputs.fairness:
            lines.append(f'### By `{f.dimension}`')
            lines.append(
                f'- **Demographic parity ratio:** {f.demographic_parity_ratio:.3f} '
                f'(pass={f.demographic_parity_pass}; 80%-rule floor=0.80)'
            )
            if f.equalized_odds_tpr_gap is not None:
                lines.append(
                    f'- **Equalized odds:** TPR gap={f.equalized_odds_tpr_gap:.3f}, '
                    f'FPR gap={f.equalized_odds_fpr_gap or 0:.3f} (pass={f.equalized_odds_pass})'
                )
            if f.predictive_parity_ppv_gap is not None:
                lines.append(
                    f'- **Predictive parity:** PPV gap={f.predictive_parity_ppv_gap:.3f} '
                    f'(pass={f.predictive_parity_pass})'
                )
            if f.violations:
                lines.append('- **Violations:**')
                for v in f.violations:
                    lines.append(f'  - {v}')
            lines.append('')
    else:
        lines.append(
            '- Fairness assessment not yet wired (FAANG 1A bias_monitor populates this).'
        )
        lines.append('')

    lines.extend(['## Stress Test (DFAST-aligned)'])
    if inputs.stress_test:
        lines.append('')
        lines.append('| Scenario | Expected Loss | Loss Rate % | Weighted-avg PD |')
        lines.append('|----------|--------------:|------------:|----------------:|')
        for s in inputs.stress_test:
            lines.append(
                f'| {s.scenario_name} | ${s.expected_loss:,.2f} | {s.loss_rate_pct:.3f}% | {s.weighted_avg_pd:.4f} |'
            )
        lines.append('')
    else:
        lines.append('- Stress test not yet wired (Phase D A9 populates this).')
        lines.append('')

    if inputs.monotonicity_constraints:
        lines.append('## Monotonicity Constraints')
        lines.append('')
        lines.append('| Feature | Direction |')
        lines.append('|---------|-----------|')
        for feature, direction in sorted(inputs.monotonicity_constraints.items()):
            lines.append(f'| `{feature}` | {direction} |')
        lines.append('')

    if inputs.adverse_action_reason_codes:
        lines.append('## Adverse Action Reason Codes (ECOA / Reg B)')
        lines.append('')
        lines.append('| Feature | Reason on adverse decision |')
        lines.append('|---------|---------------------------|')
        for feature, reason in sorted(inputs.adverse_action_reason_codes.items()):
            lines.append(f'| `{feature}` | {reason} |')
        lines.append('')

    lines.extend(
        [
            '## Limitations',
            '- Trained on synthetic data from the DGP defined in `docs/dgp_design.md`.',
            '- Real-world deployment requires re-validation against actual customer outcomes.',
            '- The bandit policy is softmax (not full RL); see scope_expansion_plan.md FAANG 1B.',
            '- See SR-11-7 sign-off checklist for governance status.',
        ]
    )

    return '\n'.join(lines)


def render_sr_11_7_checklist_markdown(inputs: ModelCardInputs) -> str:
    """SR-11-7 model-risk-management review checklist for the model risk team.

    Per Federal Reserve SR 11-7, every model deployed to production must
    have a documented review against this checklist before promotion to
    champion. This is the WORKING document the model risk team fills in.
    """
    return '\n'.join(
        [
            f'# SR-11-7 Sign-off Checklist — {inputs.model_name} v{inputs.model_version}',
            '',
            f'**MLflow run ID:** `{inputs.mlflow_run_id}`',
            '',
            '## Section 1 — Conceptual Soundness',
            '- [ ] Theory underlying the model is documented and reasonable',
            '- [ ] Variable selection rationale documented',
            '- [ ] Functional form (linear vs nonlinear) justified',
            '- [ ] Monotonicity constraints align with §4 / §8 of `01_customer_eda.ipynb`',
            '',
            '## Section 2 — Implementation Verification',
            '- [ ] Code matches design (ADR 010 synthetic RCT, ADR 012 hot challenger)',
            '- [ ] ONNX export numerical equivalence < 1e-3 of PyTorch source',
            '- [ ] Per-segment T-learners trained independently with documented seeds',
            '- [ ] Reproducibility verified — same `master_seed` produces bit-identical artifacts',
            '',
            '## Section 3 — Performance Outcome Analysis',
            '- [ ] Kendall τ > 0.6 against true uplift on OOT holdout',
            '- [ ] Calibration: HL p-value > 0.05 OR isotonic recalibration applied',
            '- [ ] Gini coefficient ≥ 0.40 (industry minimum for application PD)',
            '- [ ] KS statistic ≥ 0.30',
            '',
            '## Section 4 — Outcomes Analysis (Backtest)',
            '- [ ] OOT holdout used (not training data)',
            '- [ ] Group-constrained split — no customer leakage train→test',
            '- [ ] OPE estimates (IPS / SNIPS / DR) align within 95% CI',
            '',
            '## Section 5 — Use & Limitations',
            '- [ ] Intended use clearly documented in the model card',
            '- [ ] Out-of-scope uses listed and circulated to product team',
            '- [ ] Adverse action reason codes mapped per ECOA Reg B',
            '- [ ] Fair lending review completed (FAANG 1A bias_monitor)',
            '',
            '## Section 6 — Stress Testing',
            '- [ ] Baseline + adverse + severely adverse scenarios run (DFAST)',
            '- [ ] Portfolio EL under each scenario documented in the model card',
            '- [ ] PD floors applied per scenario (regulator guidance)',
            '',
            '## Section 7 — Governance',
            '- [ ] Model risk officer review: _______________ (date / signature)',
            '- [ ] Business owner sign-off:    _______________ (date / signature)',
            '- [ ] Audit trail in MLflow audit-events Kafka topic',
            '- [ ] Rollback procedure tested (per docs/runbooks/rollback.md)',
            '',
            '## Promotion Decision',
            '- [ ] **Approved for champion alias** OR',
            '- [ ] **Deferred** — reasons:',
            '- [ ] **Rejected** — reasons:',
            '',
            f'_Auto-generated alongside model card at {datetime.now(tz=timezone.utc).isoformat()}_',
        ]
    )
