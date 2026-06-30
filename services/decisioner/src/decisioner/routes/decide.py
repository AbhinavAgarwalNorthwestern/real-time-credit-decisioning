"""POST /decide — the platform's hot path.

Seven-step pipeline (Day 4):
  1. Feature lookup from RisingWave
  2. Segment routing
  3. Champion ONNX inference
  4. Challenger shadow inference (if alias exists)
  5. Canary routing — deterministic hash-based split decides which arm acts
  6. Bandit action selection (on the acting arm)
  7. Audit log enqueue + response
"""

from __future__ import annotations

import hashlib
import time

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from ..adverse_action import AdverseActionExplainer
from ..audit import AuditRecord, new_decision_id, now_ms
from ..bandit import Action, BanditContext, Decision, choose
from ..feature_lookup import feature_column_order, fetch_one, get_circuit_breaker
from ..inference import UpliftPrediction

router = APIRouter()


class DecideRequest(BaseModel):
    customer_id: str = Field(..., min_length=1, max_length=64)
    fraud_score: float = Field(default=0.0, ge=0.0, le=1.0)


class AdverseActionReasonResponse(BaseModel):
    feature: str
    description: str
    shap_value: float


class DecideResponse(BaseModel):
    decision_id: str
    customer_id: str
    action: str
    propensity: float
    expected_profit: float
    rationale: str
    acted_on: str  # 'champion' or 'challenger'
    adverse_action_reasons: list[AdverseActionReasonResponse] | None = None
    challenger_latency_ms: int | None = None
    latency_ms: int


def _feature_vector_hash(values: tuple[float, ...]) -> str:
    payload = ','.join(f'{v:.6g}' for v in values).encode()
    return hashlib.blake2b(payload, digest_size=16).hexdigest()


def _in_canary_cohort(customer_id: str, canary_fraction: float) -> bool:
    """Deterministic hash-based canary assignment.
    Same customer always lands on the same side for a given fraction."""
    if canary_fraction <= 0.0:
        return False
    if canary_fraction >= 1.0:
        return True
    bucket = int(hashlib.md5(customer_id.encode()).hexdigest()[:8], 16) % 10000
    return bucket < int(canary_fraction * 10000)


@router.post('/decide', response_model=DecideResponse)
async def decide(req: DecideRequest, request: Request) -> DecideResponse:
    t0 = time.monotonic()
    app = request.app

    pool = app.state.db_pool
    registry = app.state.models
    audit = app.state.audit
    canary_fraction: float = getattr(app.state, 'canary_fraction', 0.0)

    # Step 1: feature lookup
    feats = await fetch_one(pool, req.customer_id)
    if feats is None:
        if get_circuit_breaker().is_open:
            raise HTTPException(status_code=503, detail='feature store unavailable')
        raise HTTPException(status_code=404, detail='customer not found')

    # Step 2: segment routing
    segment_id = feats.segment_id if feats.segment_id is not None else 0

    # Step 3: champion inference
    all_cols = feature_column_order()
    model_cols = registry.feature_cols
    indices = [all_cols.index(c) for c in model_cols]
    fv = np.asarray([feats.values[i] for i in indices], dtype=np.float32)
    champion_uplift = registry.predict(segment_id, fv, alias='champion')

    # Step 4: challenger shadow inference (non-blocking; errors are swallowed)
    challenger_uplift: UpliftPrediction | None = None
    challenger_latency_ms: int | None = None
    challenger_loaded = registry.is_loaded('challenger')
    if challenger_loaded:
        try:
            t_chal = time.monotonic()
            challenger_uplift = registry.predict(segment_id, fv, alias='challenger')
            challenger_latency_ms = int((time.monotonic() - t_chal) * 1000)
        except Exception as exc:
            logger.warning('challenger_inference_failed', error=str(exc))

    # Step 5: canary routing
    use_challenger = challenger_uplift is not None and _in_canary_cohort(
        req.customer_id, canary_fraction
    )
    acted_on_alias = 'challenger' if use_challenger else 'champion'
    acting_uplift = challenger_uplift if use_challenger else champion_uplift

    # Step 6: bandit action selection on the acting arm
    ctx = BanditContext(
        customer_id=req.customer_id,
        segment_id=segment_id,
        uplift=acting_uplift,
        exposure=5000.0,
        fraud_score=req.fraud_score,
    )
    decision = choose(ctx)

    # Also compute challenger decision for audit (even if not acting on it)
    challenger_decision: Decision | None = None
    if challenger_uplift is not None and not use_challenger:
        challenger_ctx = BanditContext(
            customer_id=req.customer_id,
            segment_id=segment_id,
            uplift=challenger_uplift,
            exposure=5000.0,
            fraud_score=req.fraud_score,
        )
        challenger_decision = choose(challenger_ctx)

    # Step 7: adverse-action reason codes (ECOA/Reg B)
    explainer: AdverseActionExplainer | None = getattr(
        app.state,
        'adverse_action_explainer',
        None,
    )
    shap_delta: dict[str, float] | None = None
    adverse_reasons: list[AdverseActionReasonResponse] | None = None

    if explainer is not None:
        explainer.accumulate(fv)
        if decision.action == Action.NOTHING and explainer.is_ready:

            def _predict_fn(x: np.ndarray):
                return registry.predict(segment_id, x.flatten(), alias=acted_on_alias)

            explanation = explainer.explain(fv, model_cols, _predict_fn)
            if explanation is not None:
                shap_delta = {r.feature: r.shap_value for r in explanation.reasons}
                adverse_reasons = [
                    AdverseActionReasonResponse(
                        feature=r.feature,
                        description=r.description,
                        shap_value=r.shap_value,
                    )
                    for r in explanation.reasons
                ]

    # Step 8: audit log
    challenger_action_int: int | None = None
    challenger_prop: float | None = None
    if use_challenger:
        champ_ctx = BanditContext(
            customer_id=req.customer_id,
            segment_id=segment_id,
            uplift=champion_uplift,
            exposure=5000.0,
            fraud_score=req.fraud_score,
        )
        champ_decision = choose(champ_ctx)
        challenger_action_int = int(decision.action)
        challenger_prop = decision.propensity
        champion_action_int = int(champ_decision.action)
        champion_prop = champ_decision.propensity
    else:
        champion_action_int = int(decision.action)
        champion_prop = decision.propensity
        if challenger_decision is not None:
            challenger_action_int = int(challenger_decision.action)
            challenger_prop = challenger_decision.propensity

    record = AuditRecord(
        decision_id=new_decision_id(),
        customer_id=req.customer_id,
        decision_ts_ms=now_ms(),
        segment=segment_id,
        champion_model_uri='models:/uplift_per_segment@champion',
        challenger_model_uri=(
            'models:/uplift_per_segment@challenger' if challenger_loaded else None
        ),
        champion_action=champion_action_int,
        champion_propensity=champion_prop,
        challenger_action=challenger_action_int,
        challenger_propensity=challenger_prop,
        acted_on_alias=acted_on_alias,
        feature_vector_hash=_feature_vector_hash(feats.values),
        shap_delta_baseline=shap_delta,
        regulatory_flags={
            'adverse_action_eligible': decision.action == Action.NOTHING,
        },
    )
    await audit.emit(record)

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    collector = getattr(app.state, 'metrics', None)
    if collector is not None:
        collector.record(
            latency_ms=float(elapsed_ms),
            profit=decision.expected_profit,
            segment_id=segment_id,
            alias=acted_on_alias,
        )

    return DecideResponse(
        decision_id=record.decision_id,
        customer_id=req.customer_id,
        action=decision.action.name,
        propensity=decision.propensity,
        expected_profit=decision.expected_profit,
        rationale=decision.rationale,
        acted_on=acted_on_alias,
        adverse_action_reasons=adverse_reasons,
        challenger_latency_ms=challenger_latency_ms,
        latency_ms=elapsed_ms,
    )
