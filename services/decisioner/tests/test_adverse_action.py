"""Unit tests for SHAP-based adverse-action reason codes."""

from __future__ import annotations

import numpy as np
from decisioner.adverse_action import (
    REASON_CODE_MAP,
    AdverseActionExplainer,
    compute_shap_marginal,
)
from decisioner.inference import ArmPrediction, UpliftPrediction


def _linear_predict(x: np.ndarray) -> UpliftPrediction:
    """Profit is proportional to sum(features)."""
    val = float(x.flatten().sum())
    return UpliftPrediction(
        control=ArmPrediction(p_accept=0.0, delta_spend=0.0, p_default=0.0),
        treated=ArmPrediction(p_accept=0.5, delta_spend=val, p_default=0.01),
    )


def test_not_ready_before_enough_samples() -> None:
    exp = AdverseActionExplainer(background_size=10)
    rng = np.random.default_rng(seed=42)
    for _ in range(9):
        exp.accumulate(rng.standard_normal(5).astype(np.float32))
    assert exp.is_ready is False


def test_ready_after_background_size() -> None:
    exp = AdverseActionExplainer(background_size=10)
    rng = np.random.default_rng(seed=42)
    for _ in range(10):
        exp.accumulate(rng.standard_normal(5).astype(np.float32))
    assert exp.is_ready is True


def test_reasons_sorted_by_negative_shap() -> None:
    exp = AdverseActionExplainer(background_size=5)
    rng = np.random.default_rng(seed=42)
    for _ in range(5):
        exp.accumulate(rng.standard_normal(4).astype(np.float32))
    fv = np.array([-5.0, -5.0, 10.0, 10.0], dtype=np.float32)
    cols = ['velocity_5m', 'utilization', 'paydown_rate_30d', 'avg_amount_30d']
    result = exp.explain(fv, cols, _linear_predict, top_k=4)
    assert result is not None
    for i in range(len(result.reasons) - 1):
        assert result.reasons[i].shap_value <= result.reasons[i + 1].shap_value


def test_reasons_have_correct_feature_names() -> None:
    exp = AdverseActionExplainer(background_size=5)
    rng = np.random.default_rng(seed=42)
    for _ in range(5):
        exp.accumulate(rng.standard_normal(3).astype(np.float32))
    fv = np.array([-10.0, 0.0, 0.0], dtype=np.float32)
    cols = ['velocity_5m', 'utilization', 'paydown_rate_30d']
    result = exp.explain(fv, cols, _linear_predict, top_k=3)
    assert result is not None
    returned = {r.feature for r in result.reasons}
    assert returned.issubset(set(cols))
    for r in result.reasons:
        if r.feature in REASON_CODE_MAP:
            assert r.description == REASON_CODE_MAP[r.feature]


def test_marginal_shap_linear_model() -> None:
    rng = np.random.default_rng(seed=42)
    background = rng.standard_normal((50, 4)).astype(np.float32)
    bg_mean = background.mean(axis=0)
    fv = bg_mean.copy()
    fv[0] = bg_mean[0] - 5.0
    fv[2] = bg_mean[2] + 5.0
    cols = ['f0', 'f1', 'f2', 'f3']
    result = compute_shap_marginal(fv, cols, _linear_predict, background, top_k=4)
    assert result.reasons[0].feature == 'f0'
    assert result.reasons[0].shap_value < 0.0
    assert isinstance(result.baseline_profit, float)
    assert isinstance(result.customer_profit, float)


def test_explain_returns_none_before_ready() -> None:
    exp = AdverseActionExplainer(background_size=100)
    fv = np.zeros(5, dtype=np.float32)
    result = exp.explain(fv, ['a', 'b', 'c', 'd', 'e'], _linear_predict)
    assert result is None
