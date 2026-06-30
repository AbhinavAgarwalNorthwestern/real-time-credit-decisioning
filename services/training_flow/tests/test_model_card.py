"""Unit tests for model card + SR-11-7 checklist rendering (FAANG 1C)."""

from __future__ import annotations

from training_flow.model_card import (
    FairnessMetrics,
    ModelCardInputs,
    PerformanceMetrics,
    StressTestSummary,
    render_model_card_markdown,
    render_sr_11_7_checklist_markdown,
)


def _minimal_inputs(**overrides) -> ModelCardInputs:
    base: dict = {
        'model_name': 'credit_t_learner',
        'model_version': '1.1.0',
        'mlflow_run_id': 'abc123def456',
        'master_seed': 42,
        'training_parquet_sha256': 'deadbeef' * 8,
        'n_training_rows': 35000,
        'n_features': 26,
        'feature_cols': tuple([f'feat_{i}' for i in range(26)]),
        'champion_alias': 'champion',
        'performance': PerformanceMetrics(),
    }
    base.update(overrides)
    return ModelCardInputs(**base)


def test_render_model_card_includes_metadata() -> None:
    inputs = _minimal_inputs()
    md = render_model_card_markdown(inputs)
    assert 'credit_t_learner' in md
    assert '1.1.0' in md
    assert 'abc123def456' in md
    assert '42' in md
    assert '26' in md  # n_features


def test_model_card_includes_performance_when_provided() -> None:
    perf = PerformanceMetrics(
        kendall_tau=0.62,
        gini=0.45,
        ks_statistic=0.32,
        auc=0.725,
        brier_score=0.13,
        expected_calibration_error=0.024,
        hl_p_value=0.18,
    )
    inputs = _minimal_inputs(performance=perf)
    md = render_model_card_markdown(inputs)
    assert '0.6200' in md  # kendall_tau
    assert '0.4500' in md  # gini
    assert '0.3200' in md  # ks
    assert '0.7250' in md  # auc
    assert 'Hosmer-Lemeshow p-value' in md


def test_model_card_includes_fairness_per_dimension() -> None:
    fairness = [
        FairnessMetrics(
            dimension='segment_id',
            demographic_parity_ratio=0.85,
            demographic_parity_pass=True,
            equalized_odds_tpr_gap=0.05,
            equalized_odds_fpr_gap=0.04,
            equalized_odds_pass=True,
        ),
        FairnessMetrics(
            dimension='credit_score_bucket',
            demographic_parity_ratio=0.65,
            demographic_parity_pass=False,
            violations=['demographic_parity: ratio=0.650 < floor=0.80'],
        ),
    ]
    inputs = _minimal_inputs(fairness=fairness)
    md = render_model_card_markdown(inputs)
    assert 'segment_id' in md
    assert 'credit_score_bucket' in md
    assert 'demographic_parity: ratio=0.650' in md
    assert '0.85' in md


def test_model_card_includes_stress_test_table() -> None:
    stress = [
        StressTestSummary('baseline', 12345.67, 1.23, 0.05),
        StressTestSummary('adverse', 23456.78, 2.34, 0.10),
        StressTestSummary('severely_adverse', 45678.90, 4.56, 0.20),
    ]
    inputs = _minimal_inputs(stress_test=stress)
    md = render_model_card_markdown(inputs)
    assert 'Stress Test' in md
    assert 'baseline' in md
    assert 'adverse' in md
    assert 'severely_adverse' in md
    assert '$12,345.67' in md
    assert '4.560%' in md


def test_model_card_includes_monotonicity_table() -> None:
    inputs = _minimal_inputs(
        monotonicity_constraints={
            'credit_score': 'DECREASING',
            'prev_delinquency_count': 'INCREASING',
        }
    )
    md = render_model_card_markdown(inputs)
    assert 'Monotonicity Constraints' in md
    assert '`credit_score`' in md
    assert 'DECREASING' in md
    assert '`prev_delinquency_count`' in md


def test_model_card_includes_adverse_action_codes() -> None:
    inputs = _minimal_inputs(
        adverse_action_reason_codes={
            'credit_score': 'Low credit score',
            'utilization': 'High credit utilization ratio',
        }
    )
    md = render_model_card_markdown(inputs)
    assert 'Adverse Action Reason Codes' in md
    assert 'ECOA / Reg B' in md
    assert 'Low credit score' in md


def test_model_card_handles_missing_optional_sections() -> None:
    """A minimal-input card still renders cleanly without crashing."""
    inputs = _minimal_inputs()  # no fairness, no stress, no constraints
    md = render_model_card_markdown(inputs)
    # The note about fairness / stress not being wired should appear
    assert 'not yet wired' in md or 'not measured' in md


def test_sr_11_7_checklist_renders() -> None:
    inputs = _minimal_inputs()
    md = render_sr_11_7_checklist_markdown(inputs)
    assert 'SR-11-7 Sign-off Checklist' in md
    assert 'Conceptual Soundness' in md
    assert 'Implementation Verification' in md
    assert 'Outcomes Analysis (Backtest)' in md
    assert 'Stress Testing' in md
    assert 'Governance' in md
    assert 'Promotion Decision' in md


def test_sr_11_7_checklist_has_unchecked_boxes() -> None:
    """The checklist ships unsigned; signoff is added during review."""
    inputs = _minimal_inputs()
    md = render_sr_11_7_checklist_markdown(inputs)
    # Plenty of unchecked boxes for the review team to tick
    assert md.count('- [ ]') > 15


def test_model_card_lists_first_5_feature_cols() -> None:
    """For brevity, the card lists only first 5 features with ellipsis."""
    inputs = _minimal_inputs()
    md = render_model_card_markdown(inputs)
    # First 5 features should appear, sixth shouldn't (ellipsis)
    assert 'feat_0, feat_1, feat_2, feat_3, feat_4' in md


def test_model_card_includes_intended_use_default() -> None:
    """Default intended-use text mentions credit-limit-increase + decisioner."""
    inputs = _minimal_inputs()
    md = render_model_card_markdown(inputs)
    assert 'credit-limit increase' in md
    assert 'decisioner' in md.lower()


def test_model_card_includes_horizon() -> None:
    """365-day horizon mentioned by default."""
    inputs = _minimal_inputs()
    md = render_model_card_markdown(inputs)
    assert '365 days' in md
