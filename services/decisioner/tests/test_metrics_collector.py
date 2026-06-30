"""Day-4 unit tests: in-process rolling metrics collector."""

from __future__ import annotations

from decisioner.metrics_collector import MetricsCollector


class TestMetricsCollector:
    def test_empty_returns_none(self):
        mc = MetricsCollector(window_size=100)
        assert mc.current_window() is None
        assert mc.baseline_window() is None

    def test_current_window_after_enough_samples(self):
        mc = MetricsCollector(window_size=500)
        for i in range(50):
            mc.record(latency_ms=40.0 + i * 0.1, profit=10.0, is_error=False)
        w = mc.current_window(last_n=50)
        assert w is not None
        assert 40.0 <= w.p99_latency_ms <= 50.0
        assert w.mean_profit == 10.0
        assert w.error_rate == 0.0

    def test_error_rate_computed(self):
        mc = MetricsCollector(window_size=100)
        for i in range(100):
            mc.record(latency_ms=50.0, profit=5.0, is_error=(i < 10))
        w = mc.current_window(last_n=100)
        assert w is not None
        assert abs(w.error_rate - 0.10) < 0.01

    def test_baseline_needs_enough_history(self):
        mc = MetricsCollector(window_size=500)
        for _ in range(50):
            mc.record(latency_ms=50.0, profit=5.0)
        assert mc.baseline_window(last_n=200) is None

    def test_baseline_vs_current_different_windows(self):
        mc = MetricsCollector(window_size=1000)
        for _i in range(400):
            mc.record(latency_ms=40.0, profit=10.0)
        for _i in range(200):
            mc.record(latency_ms=80.0, profit=5.0)
        baseline = mc.baseline_window(last_n=200)
        current = mc.current_window(last_n=200)
        assert baseline is not None and current is not None
        assert current.p99_latency_ms > baseline.p99_latency_ms
        assert current.mean_profit < baseline.mean_profit
