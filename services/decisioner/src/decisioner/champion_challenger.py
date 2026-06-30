"""Day-4: Champion-challenger shadow scoring + canary routing + 4-eyes promotion.

Extends the request path so that when a challenger alias exists in MLflow,
its decision is computed AND logged but NOT acted upon (shadow mode).
The OPE harness (Day 6) consumes both arms from the audit log.

Canary routing: after offline validation gate + shadow scoring, a fraction
of live traffic is routed to the challenger. The fraction grows
(5% → 25% → 100%) on each interval where the regression watchdog stays
green.

4-eyes promotion: a final alias swap to champion requires two
service-account approvals captured as MLflow tags on the model version.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from loguru import logger
from mlflow.tracking import MlflowClient


class PromotionStage(str, Enum):
    OFFLINE_VALIDATION = 'offline_validation'
    SHADOW = 'shadow'
    CANARY_5 = 'canary_5'
    CANARY_25 = 'canary_25'
    CANARY_100 = 'canary_100'
    PROMOTED = 'promoted'


@dataclass(frozen=True, slots=True)
class CanaryConfig:
    challenger_fraction: float
    require_4_eyes: bool = True


def current_canary_fraction(
    client: MlflowClient, registered_model: str = 'uplift_per_segment'
) -> float:
    """Reads a `canary_fraction` tag on the challenger-aliased model
    version. Defaults to 0.0 if missing."""
    try:
        mv = client.get_model_version_by_alias(registered_model, 'challenger')
        return float(mv.tags.get('canary_fraction', '0.0'))
    except Exception:
        return 0.0


def record_approval(
    client: MlflowClient,
    registered_model: str,
    version: int,
    approver_service_account: str,
) -> None:
    """Append an approval tag. Promotion requires two distinct approvers."""
    client.set_model_version_tag(
        registered_model,
        str(version),
        f'approval_{approver_service_account}',
        'yes',
    )
    logger.info(
        'promotion_approval_logged',
        model=registered_model,
        version=version,
        approver=approver_service_account,
    )


def has_four_eyes(client: MlflowClient, registered_model: str, version: int) -> bool:
    mv = client.get_model_version(registered_model, str(version))
    approvers = {
        k for k, v in mv.tags.items() if k.startswith('approval_') and v == 'yes'
    }
    return len(approvers) >= 2
