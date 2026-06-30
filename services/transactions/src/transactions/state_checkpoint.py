"""Customer state checkpoint — persist + restore producer state across restarts.

Without checkpointing, every producer restart resets each customer's `balance`
and `session_*` fields to baseline values from `segments.py`. This causes:

1. **State discontinuity at restart**: customer A's balance drops from
   (e.g.) $1200 (accumulated through backfill) to $300 (baseline), making the
   next `events_enriched` row falsely flag `is_paydown=1`.
2. **Train-serve distribution shift**: model trained on data where customers
   accumulated up to high utilization, but serving sees customers freshly
   reset — distributions don't match for hours-to-days until live activity
   re-accumulates state to "natural" levels.

This module fixes both by:

- **Backfill mode**: writes checkpoint at the END of the backfill (one-shot,
  via `save_states_now()` at producer shutdown).
- **Live mode**: tries to load the checkpoint at startup; if found, restores
  customer balances + session state. Periodically (every `state_save_interval_s`)
  writes the current state. Also writes on SIGTERM for graceful K8s shutdown.

Storage: S3-compatible (MinIO in dev, AWS S3 in prod overlay). Cloud-agnostic
by design — same code works against any S3 backend. Per the cloud-agnostic
hard line (see [[cloud-agnostic-design]] memory), provider-specific
config lives in env vars + the K8s overlay; the code itself is portable.

Schema: JSON, versioned. ~30 KB for 1000 customers — easily fits in a single
MinIO PUT. We do NOT checkpoint the rng state — fresh rng on restart is fine
because each event samples values independently; the macro behavior
(per-customer rate distribution) is preserved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from loguru import logger

CHECKPOINT_VERSION = 1


@dataclass(frozen=True, slots=True)
class CustomerStateSnapshot:
    """Serializable view of one customer's checkpoint-relevant state."""

    customer_id: str
    balance: float
    session_end_ms: int
    session_factor: float


@dataclass(frozen=True, slots=True)
class CheckpointMetadata:
    """Top-level metadata for a saved checkpoint."""

    version: int
    seed: int
    cohort_size: int
    saved_at: str  # ISO-8601 UTC
    source_mode: str  # 'live' or 'backfill'


def _make_s3_client(
    endpoint: str,
    access_key: str,
    secret_key: str,
) -> BaseClient:
    """Build an S3 client pointed at the configured endpoint.

    Works against MinIO (dev), AWS S3 (prod), GCS HMAC (alt), or any
    S3-compatible store. Region is set to 'us-east-1' as a no-op default
    since MinIO ignores it and AWS S3 requires *some* value.
    """
    return boto3.client(
        's3',
        endpoint_url=endpoint,
        aws_access_key_id=access_key or None,
        aws_secret_access_key=secret_key or None,
        region_name='us-east-1',
    )


def _ensure_bucket(client: BaseClient, bucket: str) -> None:
    """Create the bucket if it doesn't exist. Idempotent."""
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('404', 'NoSuchBucket'):
            client.create_bucket(Bucket=bucket)
            logger.info('state_checkpoint_bucket_created bucket={}', bucket)
        else:
            raise


def save_states(
    snapshots: list[CustomerStateSnapshot],
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    object_key: str,
    seed: int,
    source_mode: str,
) -> None:
    """Serialize customer state to a JSON object in MinIO/S3.

    The file is small (~50 bytes/customer × N customers ≈ 50KB for N=1000).
    Single PUT, atomic — partial writes are not exposed because S3 writes
    are atomic per object.

    Raises ClientError on storage failure (caller decides whether fatal).
    """
    client = _make_s3_client(endpoint, access_key, secret_key)
    _ensure_bucket(client, bucket)

    metadata = CheckpointMetadata(
        version=CHECKPOINT_VERSION,
        seed=seed,
        cohort_size=len(snapshots),
        saved_at=datetime.now(tz=timezone.utc).isoformat(),
        source_mode=source_mode,
    )
    payload: dict[str, Any] = {
        'metadata': {
            'version': metadata.version,
            'seed': metadata.seed,
            'cohort_size': metadata.cohort_size,
            'saved_at': metadata.saved_at,
            'source_mode': metadata.source_mode,
        },
        'customers': [
            {
                'customer_id': s.customer_id,
                'balance': s.balance,
                'session_end_ms': s.session_end_ms,
                'session_factor': s.session_factor,
            }
            for s in snapshots
        ],
    }
    body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
    client.put_object(
        Bucket=bucket, Key=object_key, Body=body, ContentType='application/json'
    )
    logger.info(
        'state_checkpoint_saved bucket={} key={} n_customers={} bytes={}',
        bucket,
        object_key,
        len(snapshots),
        len(body),
    )


def load_states(
    *,
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
    object_key: str,
    expected_cohort_size: int,
    expected_seed: int,
) -> list[CustomerStateSnapshot] | None:
    """Load a checkpoint if present.

    Returns None when:
    - Bucket or object doesn't exist (first run, no prior backfill)
    - Checkpoint cohort_size or seed doesn't match expectations (different cohort
      → can't safely apply old balances to a new cohort)
    - Version mismatch (future-proofing for schema migration)
    - Any deserialization error

    Returning None means caller should fall back to baseline_balance.
    Never raises — checkpoint failure is non-fatal; we just lose continuity.
    """
    try:
        client = _make_s3_client(endpoint, access_key, secret_key)
        obj = client.get_object(Bucket=bucket, Key=object_key)
        body = obj['Body'].read()
        payload = json.loads(body)
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code', '')
        if code in ('NoSuchKey', 'NoSuchBucket', '404'):
            logger.info(
                'state_checkpoint_not_found bucket={} key={}', bucket, object_key
            )
            return None
        logger.warning('state_checkpoint_load_error code={} msg={}', code, exc)
        return None
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning('state_checkpoint_deserialize_error msg={}', exc)
        return None

    md = payload.get('metadata', {})
    if md.get('version') != CHECKPOINT_VERSION:
        logger.warning(
            'state_checkpoint_version_mismatch found={} expected={}',
            md.get('version'),
            CHECKPOINT_VERSION,
        )
        return None
    if md.get('cohort_size') != expected_cohort_size:
        logger.warning(
            'state_checkpoint_cohort_size_mismatch found={} expected={}',
            md.get('cohort_size'),
            expected_cohort_size,
        )
        return None
    if md.get('seed') != expected_seed:
        logger.warning(
            'state_checkpoint_seed_mismatch found={} expected={}',
            md.get('seed'),
            expected_seed,
        )
        return None

    snapshots: list[CustomerStateSnapshot] = []
    for row in payload.get('customers', []):
        snapshots.append(
            CustomerStateSnapshot(
                customer_id=str(row['customer_id']),
                balance=float(row['balance']),
                session_end_ms=int(row['session_end_ms']),
                session_factor=float(row['session_factor']),
            )
        )
    logger.info(
        'state_checkpoint_loaded bucket={} key={} n_customers={} saved_at={} source_mode={}',
        bucket,
        object_key,
        len(snapshots),
        md.get('saved_at'),
        md.get('source_mode'),
    )
    return snapshots
