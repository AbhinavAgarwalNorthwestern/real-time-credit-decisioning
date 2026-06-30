"""Unit tests for the feature-lookup circuit breaker."""

from __future__ import annotations

import time

from decisioner.feature_lookup import CircuitBreaker, CircuitState


def test_starts_closed() -> None:
    cb = CircuitBreaker()
    assert cb.state is CircuitState.CLOSED
    assert cb.allow_request() is True


def test_opens_after_threshold_failures() -> None:
    cb = CircuitBreaker(failure_threshold=3, recovery_s=30.0)
    for _ in range(3):
        cb.record_failure()
    assert cb.state is CircuitState.OPEN
    assert cb.allow_request() is False


def test_stays_closed_below_threshold() -> None:
    cb = CircuitBreaker(failure_threshold=5)
    for _ in range(4):
        cb.record_failure()
    assert cb.state is CircuitState.CLOSED


def test_success_resets_failure_count() -> None:
    cb = CircuitBreaker(failure_threshold=3)
    cb.record_failure()
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    cb.record_failure()
    assert cb.state is CircuitState.CLOSED


def test_half_open_after_recovery() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_s=0.01)
    cb.record_failure()
    assert cb.state is CircuitState.OPEN
    time.sleep(0.02)
    assert cb.state is CircuitState.HALF_OPEN
    assert cb.allow_request() is True


def test_half_open_success_closes() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_s=0.01)
    cb.record_failure()
    time.sleep(0.02)
    assert cb.state is CircuitState.HALF_OPEN
    cb.record_success()
    assert cb.state is CircuitState.CLOSED


def test_half_open_failure_reopens() -> None:
    cb = CircuitBreaker(failure_threshold=1, recovery_s=0.01)
    cb.record_failure()
    time.sleep(0.02)
    _ = cb.state  # trigger half-open transition
    cb.record_failure()
    assert cb.state is CircuitState.OPEN


def test_is_open_property() -> None:
    cb = CircuitBreaker(failure_threshold=1)
    assert cb.is_open is False
    cb.record_failure()
    assert cb.is_open is True
