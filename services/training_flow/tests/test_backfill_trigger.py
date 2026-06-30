"""Unit tests for backfill_trigger — template rendering only.

End-to-end Job submission requires a live cluster and is exercised by the
demo run, not by pytest. These tests pin the deterministic Job-naming and
template-patching behavior so refactors can't silently change either.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from training_flow.backfill_trigger import (
    BackfillRequest,
    _job_name,
    _render_job,
    request_window_days,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATE_PATH = (
    REPO_ROOT / 'deployments/base/services-finance/transactions/backfill-job.yaml'
)


def _req(seed: int = 42) -> BackfillRequest:
    return BackfillRequest(
        start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 8, tzinfo=timezone.utc),
        seed=seed,
        max_events=None,
    )


def test_job_name_is_deterministic() -> None:
    """Same (start, end, seed) → same name. Day-5 retraining depends on
    this for idempotent re-runs."""
    a = _job_name(_req(seed=42))
    b = _job_name(_req(seed=42))
    assert a == b


def test_job_name_changes_with_seed() -> None:
    assert _job_name(_req(seed=42)) != _job_name(_req(seed=43))


def test_job_name_under_63_chars() -> None:
    """K8s metadata.name has a 63-char limit. Hashed name must respect that."""
    name = _job_name(_req())
    assert len(name) <= 63, f'job name too long: {name!r}'
    assert name.startswith('transactions-backfill-')


def test_render_job_patches_placeholders() -> None:
    """Rendered Job must have no PLACEHOLDER strings left, and env vars
    must reflect the request."""
    req = _req()
    manifest = _render_job(
        TEMPLATE_PATH, req, namespace='test-ns', image='test/transactions:abc'
    )

    # Name + namespace
    assert manifest['metadata']['name'] == _job_name(req)
    assert manifest['metadata']['namespace'] == 'test-ns'

    # Image
    container = manifest['spec']['template']['spec']['containers'][0]
    assert container['image'] == 'test/transactions:abc'

    # Env vars (skip valueFrom entries that have no literal 'value' key)
    env_map = {e['name']: e['value'] for e in container['env'] if 'value' in e}
    assert env_map['TXN_MODE'] == 'backfill'
    assert env_map['TXN_START'] == '2026-06-01T00:00:00+00:00'
    assert env_map['TXN_END'] == '2026-06-08T00:00:00+00:00'
    assert env_map['TXN_SEED'] == '42'
    # max_events None → entry dropped entirely (empty string would break int|None coercion)
    assert 'TXN_MAX_EVENTS' not in env_map


def test_render_job_max_events_set() -> None:
    req = BackfillRequest(
        start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 8, tzinfo=timezone.utc),
        seed=42,
        max_events=100_000,
    )
    manifest = _render_job(TEMPLATE_PATH, req, namespace='test-ns', image='img:tag')
    container = manifest['spec']['template']['spec']['containers'][0]
    env_map = {e['name']: e['value'] for e in container['env'] if 'value' in e}
    assert env_map['TXN_MAX_EVENTS'] == '100000'


def test_request_window_days_constructs_proper_range() -> None:
    end = datetime(2026, 6, 8, 12, 0, 0, tzinfo=timezone.utc)
    req = request_window_days(days=7, seed=42, end=end)
    assert req.end == end.replace(microsecond=0)
    assert (req.end - req.start).days == 7
    assert req.seed == 42
    assert req.max_events is None


def test_render_strips_microseconds_from_window() -> None:
    """request_window_days zeroes microseconds for stable Job names."""
    end = datetime(2026, 6, 8, 12, 0, 0, 999_999, tzinfo=timezone.utc)
    req = request_window_days(days=7, seed=42, end=end)
    assert req.end.microsecond == 0


def test_template_file_exists() -> None:
    """If this fails, the manifest path drifted and runtime would surprise-fail."""
    assert TEMPLATE_PATH.exists(), f'missing template: {TEMPLATE_PATH}'


@pytest.mark.parametrize('seed', [1, 7, 42, 12345, 999999])
def test_job_name_is_valid_dns_label(seed: int) -> None:
    """K8s names must be DNS-label-safe: lowercase alphanumeric + hyphens."""
    name = _job_name(_req(seed=seed))
    assert name == name.lower()
    for ch in name:
        assert ch.isalnum() or ch == '-', f'invalid char {ch!r} in {name!r}'
    assert not name.startswith('-') and not name.endswith('-')
