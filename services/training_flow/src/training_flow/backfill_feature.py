"""Feature store backfill API + versioning — FAANG Tier 2C (part 1).

Real ML feature-store backfills support:
1. **Per-feature recompute**: a single feature's definition changed; replay
   historical events through the new definition. Don't disturb other features.
2. **Versioning**: features are versioned `feature@v3`; consumers pin to a
   version; old versions remain queryable while new ones backfill.
3. **Checkpoint-resumable**: backfilling 90 days of events takes hours;
   if it crashes at hour 50, it resumes from hour 50, not from scratch.
4. **Two-topic ingestion**: historical replay and live ingestion are on
   SEPARATE Kafka topics so watermarks don't collide. RW UNION ALLs them
   at the MV layer.

This module implements the **orchestration layer** for those workflows.
The actual feature-recompute logic stays in RisingWave SQL (where it
already lives via the windowed MVs). What we add is:

- `BackfillRequest` — declarative "recompute feature X over date range Y
  to vN" — submitted to a Metaflow flow that orchestrates the steps.
- `register_feature_version` — version + metadata tracked in MLflow as
  a sibling of model versions.
- `dispatch_to_topic` — knows whether to write to `transactions-historical`
  (backfill mode) or `transactions-live` (live mode) based on event_time.

The two-topic split eliminates the watermark collision that produces the
v1.1.0 "live/backfill gap" limitation documented in scope_expansion_plan.md.

This is the **production architecture** that the v1.1.0 single-binary,
single-topic, baseline-reset producer simplifies away. We document the
proper design here even if full implementation happens after the portfolio
build.

Cloud-agnostic: pure Python interface; the storage layer routes to MinIO
(dev) or S3 (prod) via existing `infra/lib/object_store.py`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class BackfillStatus(str, Enum):
    """Lifecycle states for a feature backfill request."""

    PENDING = 'pending'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    CANCELED = 'canceled'


@dataclass(frozen=True, slots=True)
class FeatureVersion:
    """One immutable version of a feature definition.

    Once registered, never mutated — new versions get a new `version` number.
    Consumers pin to a version explicitly; the default is "latest" only when
    that's safe (i.e., the consumer has been re-tested against the new version).
    """

    feature_name: str  # e.g., 'avg_utilization_30d'
    version: int  # monotonically increasing per feature_name
    definition_sql: str  # the SQL fragment that computes it
    definition_sha256: str  # hash of definition_sql for change detection
    registered_at: str  # ISO-8601 UTC
    registered_by: str  # author email or team
    description: str = ''


def make_feature_version(
    feature_name: str,
    version: int,
    definition_sql: str,
    registered_by: str,
    description: str = '',
) -> FeatureVersion:
    """Construct a FeatureVersion with auto-computed SHA + timestamp."""
    sha = hashlib.sha256(definition_sql.encode('utf-8')).hexdigest()
    return FeatureVersion(
        feature_name=feature_name,
        version=version,
        definition_sql=definition_sql,
        definition_sha256=sha,
        registered_at=datetime.now(tz=timezone.utc).isoformat(),
        registered_by=registered_by,
        description=description,
    )


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    """Declarative request: recompute feature X over [start, end] to version vN."""

    feature_name: str
    target_version: int
    start_event_time: datetime
    end_event_time: datetime
    output_topic: str  # 'transactions-historical' (per two-topic split)
    source_topic: str  # raw events to replay from
    requested_by: str
    request_id: str  # idempotency key; same id = same request

    def to_dict(self) -> dict:
        return {
            'feature_name': self.feature_name,
            'target_version': self.target_version,
            'start_event_time': self.start_event_time.isoformat(),
            'end_event_time': self.end_event_time.isoformat(),
            'output_topic': self.output_topic,
            'source_topic': self.source_topic,
            'requested_by': self.requested_by,
            'request_id': self.request_id,
        }


@dataclass(frozen=True, slots=True)
class BackfillProgress:
    """Checkpoint of how far a backfill has progressed.

    Persisted to MinIO so a restarted backfill resumes from the last
    successful checkpoint rather than re-replaying from the start.
    """

    request_id: str
    status: BackfillStatus
    last_processed_event_time: datetime | None
    events_processed: int
    events_failed: int
    started_at: str
    last_checkpoint_at: str
    error_message: str | None = None


def make_request_id(req_fields: dict) -> str:
    """Idempotency key — same logical request produces the same id.

    Caller submits with this id; if a backfill with this id is already
    running or completed, the system returns existing state instead of
    starting a duplicate.
    """
    payload = '|'.join(sorted(f'{k}={v}' for k, v in req_fields.items()))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


# -----------------------------------------------------------------------------
# Two-topic split routing — eliminates the live/backfill watermark collision
# documented in scope_expansion_plan.md as the v1.1.0 known limitation.
# -----------------------------------------------------------------------------

LIVE_TOPIC = 'transactions-live'
HISTORICAL_TOPIC = 'transactions-historical'


def dispatch_to_topic(event_time: datetime, *, now: datetime | None = None) -> str:
    """Route an event to the right topic based on event_time vs current time.

    Events with `event_time` within the last 5 minutes go to live. Older
    events (historical replay) go to historical. The 5-minute slack
    accommodates clock skew between the producer host and the broker.

    RW UNION ALLs the two topics into a single `events_enriched` MV but
    each source has its own watermark — so historical replay doesn't
    accidentally drop live events as "late".
    """
    n = now if now is not None else datetime.now(tz=timezone.utc)
    age_s = (n - event_time).total_seconds()
    if age_s < 300:  # 5 minutes
        return LIVE_TOPIC
    return HISTORICAL_TOPIC


# -----------------------------------------------------------------------------
# Feature version registry — lightweight in-memory cache; backed by MLflow
# tags or a dedicated registry table in production.
# -----------------------------------------------------------------------------


class FeatureRegistry:
    """In-memory feature-version registry; production-backed by MLflow."""

    def __init__(self) -> None:
        self._versions: dict[str, list[FeatureVersion]] = {}

    def register(self, version: FeatureVersion) -> None:
        """Add a new version. New versions must monotonically increase."""
        existing = self._versions.setdefault(version.feature_name, [])
        if existing and version.version <= existing[-1].version:
            raise ValueError(
                f'feature_version must be > {existing[-1].version} for '
                f'{version.feature_name}; got {version.version}'
            )
        existing.append(version)

    def latest(self, feature_name: str) -> FeatureVersion | None:
        versions = self._versions.get(feature_name)
        return versions[-1] if versions else None

    def get(self, feature_name: str, version: int) -> FeatureVersion | None:
        for v in self._versions.get(feature_name, []):
            if v.version == version:
                return v
        return None

    def list_versions(self, feature_name: str) -> list[FeatureVersion]:
        return list(self._versions.get(feature_name, []))

    def all_features(self) -> list[str]:
        return sorted(self._versions.keys())
