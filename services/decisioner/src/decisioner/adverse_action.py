"""Day-6: SHAP-based adverse-action reason codes (ECOA / Reg B compliance).

When a customer is denied (action = NOTHING), US regulations require the
lender to provide specific reasons for the adverse action. We compute SHAP
values against the model's expected-profit output and surface the top-K
features that most decreased the customer's score relative to baseline.

Uses TreeExplainer-compatible approach via ONNX runtime predictions.
Background data is sampled once at startup from the reference distribution.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from loguru import logger

from decisioner.inference import UpliftPrediction

PredictFn = Callable[[np.ndarray], UpliftPrediction]

REASON_CODE_MAP: dict[str, str] = {
    'velocity_5m': 'High recent transaction frequency',
    'velocity_1h': 'Elevated short-term spending velocity',
    'velocity_24h': 'High daily transaction count',
    'avg_amount_30d': 'Average transaction amount outside expected range',
    'utilization': 'High credit utilization ratio',
    'pct_cash_advance_30d': 'Elevated cash advance activity',
    'paydown_rate_30d': 'Low monthly payment rate',
    'mcc_entropy_24h': 'Unusual merchant category diversity',
    # Customer-level attributes (from customer_attributes MV).
    'credit_score': 'Low credit score',
    'annual_income': 'Insufficient income for requested credit',
    'account_tenure_months': 'Limited account history',
    'n_products': 'Limited product relationship',
    'prev_delinquency_count': 'Previous delinquency on record',
}

DEFAULT_REASON = 'Model factor outside favorable range'


@dataclass(frozen=True, slots=True)
class AdverseActionReason:
    feature: str
    description: str
    shap_value: float
    feature_value: float


@dataclass(frozen=True, slots=True)
class AdverseActionExplanation:
    reasons: list[AdverseActionReason]
    baseline_profit: float
    customer_profit: float


def compute_shap_marginal(
    feature_vector: np.ndarray,
    feature_cols: list[str],
    predict_fn: PredictFn,
    background: np.ndarray,
    top_k: int = 4,
) -> AdverseActionExplanation:
    """Approximate SHAP via marginal contribution (mean-baseline method).

    For each feature i, replaces x_i with the background mean and measures
    the change in expected profit. This is the "break-down" approximation —
    exact for additive models, directionally correct for non-linear.

    Much faster than KernelSHAP (~n_features forward passes vs O(2^n)),
    acceptable for real-time serving where we need <5ms for explanations.
    """
    x = feature_vector.astype(np.float32).reshape(1, -1)
    baseline_x = background.mean(axis=0).astype(np.float32).reshape(1, -1)

    customer_pred = predict_fn(x)
    baseline_pred = predict_fn(baseline_x)

    customer_profit = customer_pred.expected_profit_treated(5000.0)
    baseline_profit = baseline_pred.expected_profit_treated(5000.0)

    contributions: list[tuple[str, float, float]] = []
    for i, col in enumerate(feature_cols):
        perturbed = x.copy()
        perturbed[0, i] = baseline_x[0, i]
        perturbed_pred = predict_fn(perturbed)
        perturbed_profit = perturbed_pred.expected_profit_treated(5000.0)
        marginal = customer_profit - perturbed_profit
        contributions.append((col, marginal, float(x[0, i])))

    contributions.sort(key=lambda t: t[1])

    reasons = []
    for col, shap_val, feat_val in contributions[:top_k]:
        if shap_val >= 0:
            break
        reasons.append(
            AdverseActionReason(
                feature=col,
                description=REASON_CODE_MAP.get(col, DEFAULT_REASON),
                shap_value=round(shap_val, 4),
                feature_value=round(feat_val, 4),
            )
        )

    return AdverseActionExplanation(
        reasons=reasons,
        baseline_profit=round(baseline_profit, 4),
        customer_profit=round(customer_profit, 4),
    )


class AdverseActionExplainer:
    """Stateful explainer — holds background data and provides per-request explanations."""

    def __init__(self, background_size: int = 100) -> None:
        self._background: np.ndarray | None = None
        self._background_size = background_size
        self._samples_collected: list[np.ndarray] = []

    @property
    def is_ready(self) -> bool:
        return self._background is not None

    def accumulate(self, feature_vector: np.ndarray) -> None:
        """Collect background samples from live traffic until we have enough."""
        if self._background is not None:
            return
        self._samples_collected.append(feature_vector.copy())
        if len(self._samples_collected) >= self._background_size:
            self._background = np.stack(self._samples_collected, axis=0)
            self._samples_collected = []
            logger.info(
                'adverse_action_background_ready size={}', self._background.shape[0]
            )

    def explain(
        self,
        feature_vector: np.ndarray,
        feature_cols: list[str],
        predict_fn: PredictFn,
        top_k: int = 4,
    ) -> AdverseActionExplanation | None:
        """Returns adverse-action reasons or None if background not ready."""
        if self._background is None:
            return None
        return compute_shap_marginal(
            feature_vector,
            feature_cols,
            predict_fn,
            self._background,
            top_k,
        )
