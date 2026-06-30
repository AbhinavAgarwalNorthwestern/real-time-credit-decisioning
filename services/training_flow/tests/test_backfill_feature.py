"""Unit tests for feature-backfill API (FAANG 2C)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from training_flow.backfill_feature import (
    HISTORICAL_TOPIC,
    LIVE_TOPIC,
    BackfillRequest,
    BackfillStatus,
    FeatureRegistry,
    dispatch_to_topic,
    make_feature_version,
    make_request_id,
)


def test_make_feature_version_computes_sha() -> None:
    fv = make_feature_version(
        feature_name='velocity_5m',
        version=1,
        definition_sql='SELECT COUNT(*) FROM transactions GROUP BY customer_id',
        registered_by='test@example.com',
    )
    assert fv.feature_name == 'velocity_5m'
    assert fv.version == 1
    assert len(fv.definition_sha256) == 64  # SHA256 hex digest
    assert fv.registered_at  # populated


def test_two_identical_definitions_get_same_sha() -> None:
    fv1 = make_feature_version('x', 1, 'SELECT 1', 'me')
    fv2 = make_feature_version('y', 5, 'SELECT 1', 'them')
    assert fv1.definition_sha256 == fv2.definition_sha256


def test_different_definitions_get_different_shas() -> None:
    fv1 = make_feature_version('x', 1, 'SELECT 1', 'me')
    fv2 = make_feature_version('x', 2, 'SELECT 2', 'me')
    assert fv1.definition_sha256 != fv2.definition_sha256


def test_make_request_id_deterministic() -> None:
    """Same request fields → same id (idempotency)."""
    fields = {'feature_name': 'velocity_5m', 'target_version': 3, 'start': '2026-01-01'}
    a = make_request_id(fields)
    b = make_request_id(fields)
    assert a == b
    assert len(a) == 16


def test_make_request_id_different_for_different_requests() -> None:
    a = make_request_id({'feature_name': 'a', 'v': 1})
    b = make_request_id({'feature_name': 'b', 'v': 1})
    assert a != b


def test_backfill_request_to_dict_roundtrip() -> None:
    req = BackfillRequest(
        feature_name='avg_utilization_30d',
        target_version=3,
        start_event_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_event_time=datetime(2026, 2, 1, tzinfo=timezone.utc),
        output_topic=HISTORICAL_TOPIC,
        source_topic=LIVE_TOPIC,
        requested_by='ml-team',
        request_id='abc123',
    )
    d = req.to_dict()
    assert d['feature_name'] == 'avg_utilization_30d'
    assert d['target_version'] == 3
    assert d['output_topic'] == HISTORICAL_TOPIC


def test_dispatch_recent_event_to_live() -> None:
    """Events within 5 min of 'now' → live topic."""
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(seconds=120)  # 2 min ago
    assert dispatch_to_topic(recent, now=now) == LIVE_TOPIC


def test_dispatch_old_event_to_historical() -> None:
    """Events older than 5 min → historical topic."""
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(days=2)
    assert dispatch_to_topic(old, now=now) == HISTORICAL_TOPIC


def test_dispatch_boundary_5_min() -> None:
    """Exactly at the 5-min boundary → historical (≥ 300 s wins)."""
    now = datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc)
    edge = now - timedelta(seconds=300)
    assert dispatch_to_topic(edge, now=now) == HISTORICAL_TOPIC


def test_registry_register_and_retrieve() -> None:
    reg = FeatureRegistry()
    fv = make_feature_version('velocity_5m', 1, 'SELECT 1', 'me')
    reg.register(fv)
    assert reg.latest('velocity_5m') == fv
    assert reg.get('velocity_5m', 1) == fv


def test_registry_versions_must_increase() -> None:
    reg = FeatureRegistry()
    fv1 = make_feature_version('x', 1, 'SELECT 1', 'me')
    fv2_dup = make_feature_version('x', 1, 'SELECT 2', 'me')  # same version
    reg.register(fv1)
    with pytest.raises(ValueError, match='must be'):
        reg.register(fv2_dup)


def test_registry_versions_monotonic() -> None:
    """Re-registering a lower version raises."""
    reg = FeatureRegistry()
    fv1 = make_feature_version('x', 1, 'SELECT 1', 'me')
    fv2 = make_feature_version('x', 2, 'SELECT 2', 'me')
    reg.register(fv1)
    reg.register(fv2)
    # Now try to register v1 again — should fail
    fv1_again = make_feature_version('x', 1, 'SELECT 3', 'me')
    with pytest.raises(ValueError):
        reg.register(fv1_again)


def test_registry_lists_all_features() -> None:
    reg = FeatureRegistry()
    reg.register(make_feature_version('a', 1, 'SELECT 1', 'me'))
    reg.register(make_feature_version('b', 1, 'SELECT 2', 'me'))
    reg.register(make_feature_version('c', 1, 'SELECT 3', 'me'))
    assert reg.all_features() == ['a', 'b', 'c']


def test_registry_list_versions_returns_all() -> None:
    reg = FeatureRegistry()
    reg.register(make_feature_version('x', 1, 'SELECT 1', 'me'))
    reg.register(make_feature_version('x', 2, 'SELECT 2', 'me'))
    reg.register(make_feature_version('x', 3, 'SELECT 3', 'me'))
    versions = reg.list_versions('x')
    assert [v.version for v in versions] == [1, 2, 3]


def test_backfill_status_enum_values() -> None:
    """Status enum values are stable strings safe for serialization."""
    assert BackfillStatus.PENDING.value == 'pending'
    assert BackfillStatus.RUNNING.value == 'running'
    assert BackfillStatus.COMPLETED.value == 'completed'
    assert BackfillStatus.FAILED.value == 'failed'
    assert BackfillStatus.CANCELED.value == 'canceled'


def test_topic_names_constants() -> None:
    """The two-topic split uses these names."""
    assert LIVE_TOPIC == 'transactions-live'
    assert HISTORICAL_TOPIC == 'transactions-historical'
