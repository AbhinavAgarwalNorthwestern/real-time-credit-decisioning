"""Hot-challenger retraining strategy — adopted per ADR 012.

Rationale: when drift fires, training a fresh model from scratch takes
~10-30 minutes (depending on backfill window + Optuna trials). During
that window the champion is making decisions on stale ground.

The hot-challenger pattern eliminates this gap:
  - A retraining job runs on a SHORT recurring cadence (default: every
    2 hours), regardless of whether drift has fired.
  - Each run produces a new model version, registered with the
    `latest_candidate` alias. No live traffic touches it.
  - When the drift monitor fires, the orchestrator:
      1. Promotes `latest_candidate` to `challenger` (instant)
      2. Begins shadow scoring + canary routing per Day-4 logic
  - Result: time-to-challenger ≈ seconds, not minutes.

Three orchestration triggers:
  - SCHEDULED — every cadence_minutes, run RetrainingFlow, label result
    as `latest_candidate`. The default cadence (2h) is short enough that
    the candidate is always "fresh enough" to deploy without re-training.
  - DRIFT — drift_monitor emits an event. Orchestrator promotes the
    latest_candidate to challenger if one exists; if not, runs an
    immediate retraining first.
  - MANUAL — operator calls `metaflow run retraining_flow/flow.py`.

Lifecycle of an MLflow model version:
   created → latest_candidate → challenger → champion → archived
                  ↑                  ↑           ↑
              (every cadence)     (drift fired   (4-eyes
                                   or scheduled    + canary
                                   promotion)     success)

The hot-challenger pattern costs ~12 retraining runs/day (every 2h).
At ~$0.30/run in Metaflow @kubernetes, that's <$5/day — trivially
worth eliminating the drift-to-deploy gap. Cost scales with backfill
window; for production this is sized against the SLA on drift
response time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from loguru import logger
from mlflow.tracking import MlflowClient

REGISTERED_MODEL_NAME: Final[str] = 'uplift_per_segment'

CANDIDATE_ALIAS: Final[str] = 'latest_candidate'
CHALLENGER_ALIAS: Final[str] = 'challenger'
CHAMPION_ALIAS: Final[str] = 'champion'


class Trigger(str, Enum):
    SCHEDULED = 'scheduled'
    DRIFT = 'drift'
    MANUAL = 'manual'


@dataclass(frozen=True, slots=True)
class HotChallengerConfig:
    cadence_minutes: int = 120  # every 2h
    max_candidate_age_minutes: int = 360  # 6h — candidate must be fresher than this
    require_dgp_gate_passing: bool = True


def latest_candidate_version(client: MlflowClient) -> int | None:
    try:
        mv = client.get_model_version_by_alias(REGISTERED_MODEL_NAME, CANDIDATE_ALIAS)
        return int(mv.version)
    except Exception:
        return None


def candidate_age_minutes(client: MlflowClient) -> float | None:
    from datetime import datetime, timezone

    v = latest_candidate_version(client)
    if v is None:
        return None
    mv = client.get_model_version(REGISTERED_MODEL_NAME, str(v))
    created_ms = mv.creation_timestamp
    now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    return (now_ms - created_ms) / 60_000.0


def promote_candidate_to_challenger(
    client: MlflowClient, config: HotChallengerConfig
) -> bool:
    """Instantly promote the latest candidate to challenger alias.

    Returns False if there's no fresh candidate (caller should fall back
    to a synchronous retraining run).
    """
    v = latest_candidate_version(client)
    if v is None:
        logger.warning('promote_candidate_no_candidate_exists')
        return False
    age = candidate_age_minutes(client)
    if age is None or age > config.max_candidate_age_minutes:
        logger.warning(
            'promote_candidate_stale age_minutes={} max={}',
            age,
            config.max_candidate_age_minutes,
        )
        return False
    # Tag freshness for audit
    client.set_model_version_tag(
        REGISTERED_MODEL_NAME,
        str(v),
        'promoted_at_minutes_age',
        f'{age:.1f}',
    )
    # Tag with starting canary fraction so Day-4 canary logic picks up
    client.set_model_version_tag(
        REGISTERED_MODEL_NAME,
        str(v),
        'canary_fraction',
        '0.05',
    )
    client.set_registered_model_alias(
        REGISTERED_MODEL_NAME,
        CHALLENGER_ALIAS,
        v,
    )
    logger.info('challenger_promoted version={} age_minutes={}', v, age)
    return True


def on_drift_event(
    client: MlflowClient, config: HotChallengerConfig, drift_summary: dict[str, float]
) -> dict[str, object]:
    """Entry point called by the drift_monitor when a signal fires.

    Returns a dict explaining what was done — logged into MLflow run tags.
    """
    logger.info('drift_event_received drift_summary={}', drift_summary)
    promoted = promote_candidate_to_challenger(client, config)
    if promoted:
        return {
            'trigger': Trigger.DRIFT.value,
            'action': 'promoted_candidate_to_challenger',
            'time_to_challenger_s': 0,  # instant alias swap
        }
    # No fresh candidate — fall back to synchronous retraining (slow path).
    logger.warning('no_fresh_candidate_falling_back_to_synchronous_retrain')
    # Caller (Argo Events or the watchdog) is expected to invoke the
    # Metaflow flow synchronously here.
    return {
        'trigger': Trigger.DRIFT.value,
        'action': 'fallback_synchronous_retrain',
        'time_to_challenger_s': 600,  # estimate — depends on backfill window
    }
