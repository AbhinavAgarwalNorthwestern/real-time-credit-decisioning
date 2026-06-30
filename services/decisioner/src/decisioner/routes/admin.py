"""Admin routes — promotion, canary control, watchdog status.

Not on the hot path. Protected by service-account auth in production
(K8s NetworkPolicy + RBAC); open in dev for smoke testing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from ..champion_challenger import (
    has_four_eyes,
    record_approval,
)
from ..inference import REGISTERED_MODEL_NAME

router = APIRouter(prefix='/admin', tags=['admin'])


class ApprovalRequest(BaseModel):
    approver: str = Field(..., min_length=1, max_length=128)


class ApprovalResponse(BaseModel):
    model: str
    version: int
    approver: str
    has_four_eyes: bool


class PromoteResponse(BaseModel):
    promoted: bool
    old_champion_version: int | None
    new_champion_version: int
    message: str


class CanaryRequest(BaseModel):
    fraction: float = Field(..., ge=0.0, le=1.0)


class WatchdogStatus(BaseModel):
    canary_fraction: float
    current_metrics: dict | None
    baseline_metrics: dict | None


@router.post('/approve', response_model=ApprovalResponse)
async def approve_challenger(
    req: ApprovalRequest, request: Request
) -> ApprovalResponse:
    """Record a 4-eyes approval on the current challenger version."""
    client = request.app.state.mlflow_client
    try:
        mv = client.get_model_version_by_alias(REGISTERED_MODEL_NAME, 'challenger')
    except Exception as exc:
        raise HTTPException(404, f'No challenger alias found: {exc}') from exc
    version = int(mv.version)
    record_approval(client, REGISTERED_MODEL_NAME, version, req.approver)
    four_eyes = has_four_eyes(client, REGISTERED_MODEL_NAME, version)
    return ApprovalResponse(
        model=REGISTERED_MODEL_NAME,
        version=version,
        approver=req.approver,
        has_four_eyes=four_eyes,
    )


@router.post('/promote', response_model=PromoteResponse)
async def promote_challenger(request: Request) -> PromoteResponse:
    """Swap challenger → champion alias. Requires 4-eyes approval."""
    client = request.app.state.mlflow_client
    try:
        challenger_mv = client.get_model_version_by_alias(
            REGISTERED_MODEL_NAME,
            'challenger',
        )
    except Exception as exc:
        raise HTTPException(404, f'No challenger alias found: {exc}') from exc

    version = int(challenger_mv.version)
    if not has_four_eyes(client, REGISTERED_MODEL_NAME, version):
        raise HTTPException(
            403,
            'Promotion requires 4-eyes approval (2 distinct approvers)',
        )

    try:
        old_mv = client.get_model_version_by_alias(
            REGISTERED_MODEL_NAME,
            'champion',
        )
        old_version = int(old_mv.version)
    except Exception:
        old_version = None

    client.set_registered_model_alias(
        REGISTERED_MODEL_NAME,
        'champion',
        str(version),
    )
    logger.info('champion_promoted', old=old_version, new=version)

    request.app.state.canary_fraction = 0.0

    return PromoteResponse(
        promoted=True,
        old_champion_version=old_version,
        new_champion_version=version,
        message=f'Version {version} is now champion',
    )


@router.post('/canary', response_model=dict)
async def set_canary(req: CanaryRequest, request: Request) -> dict:
    """Override canary fraction (dev/testing shortcut)."""
    client = request.app.state.mlflow_client
    try:
        mv = client.get_model_version_by_alias(REGISTERED_MODEL_NAME, 'challenger')
        client.set_model_version_tag(
            REGISTERED_MODEL_NAME,
            str(mv.version),
            'canary_fraction',
            str(req.fraction),
        )
    except Exception as exc:
        raise HTTPException(404, f'No challenger alias: {exc}') from exc
    request.app.state.canary_fraction = req.fraction
    logger.info('canary_fraction_set', fraction=req.fraction)
    return {'canary_fraction': req.fraction}


@router.get('/watchdog', response_model=WatchdogStatus)
async def watchdog_status(request: Request) -> WatchdogStatus:
    collector = getattr(request.app.state, 'metrics', None)
    current = collector.current_window() if collector else None
    baseline = collector.baseline_window() if collector else None
    return WatchdogStatus(
        canary_fraction=getattr(request.app.state, 'canary_fraction', 0.0),
        current_metrics={
            'p99_latency_ms': current.p99_latency_ms,
            'mean_profit': current.mean_profit,
            'error_rate': current.error_rate,
        }
        if current
        else None,
        baseline_metrics={
            'p99_latency_ms': baseline.p99_latency_ms,
            'mean_profit': baseline.mean_profit,
            'error_rate': baseline.error_rate,
        }
        if baseline
        else None,
    )
