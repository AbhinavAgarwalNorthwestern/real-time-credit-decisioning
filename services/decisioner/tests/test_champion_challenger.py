"""Day-4 unit tests: canary routing, shadow scoring, rollback logic."""

from __future__ import annotations

import pytest
from decisioner.rollback_watchdog import (
    WatchdogThresholds,
    WindowMetrics,
    should_rollback,
)
from decisioner.routes.decide import _in_canary_cohort


class TestCanaryRouting:
    """Hash-based deterministic canary assignment."""

    def test_zero_fraction_never_canary(self):
        for i in range(100):
            assert _in_canary_cohort(f'cust-{i:06d}', 0.0) is False

    def test_full_fraction_always_canary(self):
        for i in range(100):
            assert _in_canary_cohort(f'cust-{i:06d}', 1.0) is True

    def test_deterministic_same_customer(self):
        cid = 'cust-000025'
        result = _in_canary_cohort(cid, 0.5)
        for _ in range(50):
            assert _in_canary_cohort(cid, 0.5) == result

    def test_fraction_05_roughly_half(self):
        n = 10000
        in_canary = sum(_in_canary_cohort(f'cust-{i:06d}', 0.5) for i in range(n))
        assert 4500 < in_canary < 5500

    def test_fraction_005_roughly_five_percent(self):
        n = 10000
        in_canary = sum(_in_canary_cohort(f'cust-{i:06d}', 0.05) for i in range(n))
        assert 350 < in_canary < 650

    def test_monotonic_inclusion(self):
        """If a customer is in canary at fraction=0.05, they're also in at 0.25."""
        cids_at_05 = {
            f'cust-{i:06d}'
            for i in range(1000)
            if _in_canary_cohort(f'cust-{i:06d}', 0.05)
        }
        cids_at_25 = {
            f'cust-{i:06d}'
            for i in range(1000)
            if _in_canary_cohort(f'cust-{i:06d}', 0.25)
        }
        assert cids_at_05.issubset(cids_at_25)


class TestShouldRollback:
    """Watchdog rollback decision logic."""

    @pytest.fixture()
    def thresholds(self) -> WatchdogThresholds:
        return WatchdogThresholds(
            p99_latency_max_degradation=0.20,
            profit_max_drop=0.10,
            error_rate_max=0.01,
        )

    @pytest.fixture()
    def baseline(self) -> WindowMetrics:
        return WindowMetrics(p99_latency_ms=50.0, mean_profit=100.0, error_rate=0.001)

    def test_no_rollback_healthy(self, thresholds, baseline):
        current = WindowMetrics(p99_latency_ms=55.0, mean_profit=95.0, error_rate=0.005)
        do_it, reason = should_rollback(current, baseline, thresholds)
        assert do_it is False
        assert reason == ''

    def test_rollback_on_error_rate(self, thresholds, baseline):
        current = WindowMetrics(p99_latency_ms=50.0, mean_profit=100.0, error_rate=0.02)
        do_it, reason = should_rollback(current, baseline, thresholds)
        assert do_it is True
        assert 'error_rate' in reason

    def test_rollback_on_latency_degradation(self, thresholds, baseline):
        current = WindowMetrics(
            p99_latency_ms=65.0, mean_profit=100.0, error_rate=0.001
        )
        do_it, reason = should_rollback(current, baseline, thresholds)
        assert do_it is True
        assert 'p99' in reason

    def test_rollback_on_profit_drop(self, thresholds, baseline):
        current = WindowMetrics(p99_latency_ms=50.0, mean_profit=85.0, error_rate=0.001)
        do_it, reason = should_rollback(current, baseline, thresholds)
        assert do_it is True
        assert 'mean_profit' in reason

    def test_no_rollback_at_boundary(self, thresholds, baseline):
        current = WindowMetrics(p99_latency_ms=60.0, mean_profit=90.0, error_rate=0.01)
        do_it, _ = should_rollback(current, baseline, thresholds)
        assert do_it is False

    def test_baseline_zero_latency_no_divide_by_zero(self, thresholds):
        baseline = WindowMetrics(p99_latency_ms=0.0, mean_profit=100.0, error_rate=0.0)
        current = WindowMetrics(p99_latency_ms=999.0, mean_profit=100.0, error_rate=0.0)
        do_it, _ = should_rollback(current, baseline, thresholds)
        assert do_it is False

    def test_baseline_zero_profit_no_divide_by_zero(self, thresholds):
        baseline = WindowMetrics(p99_latency_ms=50.0, mean_profit=0.0, error_rate=0.0)
        current = WindowMetrics(p99_latency_ms=50.0, mean_profit=-10.0, error_rate=0.0)
        do_it, _ = should_rollback(current, baseline, thresholds)
        assert do_it is False
