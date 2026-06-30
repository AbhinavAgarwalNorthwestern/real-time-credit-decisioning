"""Bias monitor service entry point.

Consumes `decisions` + `outcomes` Kafka topics, joins them on (decision_id,
customer_id), buffers in a rolling window, periodically computes the
fairness report, and exports per-group metrics to Prometheus.

Architecturally minimal: single-async-loop, dict-based join (decisions wait
for matching outcomes for up to `window_size_decisions` events), fairness
evaluation every `eval_interval_s`. Production would split this into a
proper streaming join via Kafka Streams or Flink; for our scale this is
sufficient and easier to reason about.

Cloud-agnostic per [[cloud-agnostic-design]] memory: aiokafka + prometheus_client,
no provider-specific code. AWS overlay swaps the broker endpoint via
ConfigMap; the code is identical.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from collections import deque
from typing import Any

import numpy as np
from aiokafka import AIOKafkaConsumer
from loguru import logger
from prometheus_client import Gauge, start_http_server

from bias_monitor.config import BiasMonitorSettings
from bias_monitor.fairness import (
    compute_fairness_report,
    credit_score_buckets,
    report_summary_lines,
)

# Prometheus metrics — exported per group + per dimension
_dp_ratio = Gauge(
    'bias_demographic_parity_ratio',
    'Demographic parity ratio (smallest pred-rate / largest); 80% rule floor',
    ['dimension'],
)
_eodds_tpr_gap = Gauge(
    'bias_equalized_odds_tpr_gap',
    'Max absolute TPR gap across groups',
    ['dimension'],
)
_eodds_fpr_gap = Gauge(
    'bias_equalized_odds_fpr_gap',
    'Max absolute FPR gap across groups',
    ['dimension'],
)
_ppv_gap = Gauge(
    'bias_predictive_parity_ppv_gap',
    'Max absolute PPV gap across groups',
    ['dimension'],
)
_pos_pred_rate = Gauge(
    'bias_positive_prediction_rate',
    'Positive-prediction rate per group',
    ['dimension', 'group_id'],
)
_tpr = Gauge('bias_true_positive_rate', 'TPR per group', ['dimension', 'group_id'])
_fpr = Gauge('bias_false_positive_rate', 'FPR per group', ['dimension', 'group_id'])
_ppv = Gauge(
    'bias_positive_predictive_value',
    'PPV per group',
    ['dimension', 'group_id'],
)
_violations = Gauge(
    'bias_violations_total',
    'Number of fairness violations in the latest evaluation',
    ['dimension'],
)
_records_buffered = Gauge(
    'bias_records_buffered',
    'Number of (decision, outcome) joined records in the rolling window',
)


_should_stop = False


def _on_signal(signum: int, _frame: Any) -> None:
    global _should_stop
    logger.info('Received signal {}; stopping', signum)
    _should_stop = True


class JoinedBuffer:
    """Rolling window of joined (decision, outcome) records.

    Decisions arrive on one stream, outcomes on another. We hold pending
    decisions in a dict keyed by (decision_id, customer_id) and emit a
    joined record when the matching outcome arrives. Joined records are
    appended to a deque sized at `max_size`.
    """

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._pending: dict[tuple[str, str], dict[str, Any]] = {}
        self._joined: deque[dict[str, Any]] = deque(maxlen=max_size)

    def add_decision(self, decision: dict[str, Any]) -> None:
        key = (str(decision.get('decision_id')), str(decision.get('customer_id')))
        self._pending[key] = decision

    def add_outcome(self, outcome: dict[str, Any]) -> dict[str, Any] | None:
        key = (str(outcome.get('decision_id')), str(outcome.get('customer_id')))
        decision = self._pending.pop(key, None)
        if decision is None:
            return None
        joined = {**decision, **outcome}
        self._joined.append(joined)
        return joined

    def __len__(self) -> int:
        return len(self._joined)

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
        """Return (y_true, y_pred, segment_id, credit_score) — None if empty.

        Schema assumptions:
          - decision payload includes `predicted_pd` (float) and `segment_id` (int)
            and `credit_score` (int) and `customer_id` (str)
          - outcome payload includes `defaulted` (0/1 int)
        """
        if not self._joined:
            return None
        y_true: list[int] = []
        y_pred: list[float] = []
        seg_ids: list[int] = []
        scores: list[int] = []
        for r in self._joined:
            try:
                y_true.append(int(r['defaulted']))
                y_pred.append(float(r['predicted_pd']))
                seg_ids.append(int(r['segment_id']))
                scores.append(int(r['credit_score']))
            except (KeyError, TypeError, ValueError):
                continue
        if not y_true:
            return None
        return (
            np.array(y_true, dtype=int),
            np.array(y_pred, dtype=float),
            np.array(seg_ids, dtype=int),
            np.array(scores, dtype=int),
        )


def _export_report(report, dimension: str) -> None:
    """Push one FairnessReport to Prometheus gauges."""
    _dp_ratio.labels(dimension=dimension).set(report.demographic_parity_ratio)
    if report.equalized_odds_tpr_gap is not None:
        _eodds_tpr_gap.labels(dimension=dimension).set(report.equalized_odds_tpr_gap)
    if report.equalized_odds_fpr_gap is not None:
        _eodds_fpr_gap.labels(dimension=dimension).set(report.equalized_odds_fpr_gap)
    if report.predictive_parity_ppv_gap is not None:
        _ppv_gap.labels(dimension=dimension).set(report.predictive_parity_ppv_gap)
    _violations.labels(dimension=dimension).set(len(report.violations))
    for m in report.per_group:
        gid = m.group_id
        _pos_pred_rate.labels(dimension=dimension, group_id=gid).set(
            m.positive_pred_rate
        )
        if m.true_positive_rate is not None:
            _tpr.labels(dimension=dimension, group_id=gid).set(m.true_positive_rate)
        if m.false_positive_rate is not None:
            _fpr.labels(dimension=dimension, group_id=gid).set(m.false_positive_rate)
        if m.positive_predictive_value is not None:
            _ppv.labels(dimension=dimension, group_id=gid).set(
                m.positive_predictive_value
            )


async def _consume_decisions(consumer: AIOKafkaConsumer, buf: JoinedBuffer) -> None:
    async for msg in consumer:
        if _should_stop:
            return
        try:
            payload = json.loads(msg.value)
            buf.add_decision(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning('decisions_parse_error msg={}', exc)


async def _consume_outcomes(consumer: AIOKafkaConsumer, buf: JoinedBuffer) -> None:
    async for msg in consumer:
        if _should_stop:
            return
        try:
            payload = json.loads(msg.value)
            buf.add_outcome(payload)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning('outcomes_parse_error msg={}', exc)


async def _eval_loop(buf: JoinedBuffer, settings: BiasMonitorSettings) -> None:
    """Periodically compute fairness report from the buffered data."""
    while not _should_stop:
        await asyncio.sleep(settings.eval_interval_s)
        _records_buffered.set(len(buf))
        arrays = buf.to_arrays()
        if arrays is None:
            logger.info('bias_eval_skipped reason=empty_buffer')
            continue
        y_true, y_pred, seg_ids, scores = arrays
        # Segment-id report
        seg_report = compute_fairness_report(
            y_true,
            y_pred,
            seg_ids,
            group_dimension='segment_id',
            eighty_pct_rule_floor=settings.eighty_pct_rule_floor,
            equalized_odds_tol=settings.equalized_odds_tolerance,
            predictive_parity_tol=settings.predictive_parity_tolerance,
        )
        _export_report(seg_report, dimension='segment_id')
        for line in report_summary_lines(seg_report):
            logger.info(line)

        # Credit-score bucket report
        buckets = credit_score_buckets(scores, n_buckets=5)
        bucket_report = compute_fairness_report(
            y_true,
            y_pred,
            buckets,
            group_dimension='credit_score_bucket',
            eighty_pct_rule_floor=settings.eighty_pct_rule_floor,
            equalized_odds_tol=settings.equalized_odds_tolerance,
            predictive_parity_tol=settings.predictive_parity_tolerance,
        )
        _export_report(bucket_report, dimension='credit_score_bucket')
        for line in report_summary_lines(bucket_report):
            logger.info(line)


async def _async_main(settings: BiasMonitorSettings) -> None:
    buf = JoinedBuffer(max_size=settings.window_size_decisions)

    decisions_consumer = AIOKafkaConsumer(
        settings.decisions_topic,
        bootstrap_servers=settings.kafka_broker,
        group_id=settings.consumer_group,
        auto_offset_reset='latest',
    )
    outcomes_consumer = AIOKafkaConsumer(
        settings.outcomes_topic,
        bootstrap_servers=settings.kafka_broker,
        group_id=settings.consumer_group,
        auto_offset_reset='latest',
    )
    await decisions_consumer.start()
    await outcomes_consumer.start()
    logger.info(
        'bias_monitor_started broker={} decisions={} outcomes={}',
        settings.kafka_broker,
        settings.decisions_topic,
        settings.outcomes_topic,
    )

    try:
        await asyncio.gather(
            _consume_decisions(decisions_consumer, buf),
            _consume_outcomes(outcomes_consumer, buf),
            _eval_loop(buf, settings),
        )
    finally:
        await decisions_consumer.stop()
        await outcomes_consumer.stop()


def main() -> None:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    settings = BiasMonitorSettings()
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)

    start_http_server(settings.prom_http_port)
    logger.info('prometheus_http_started port={}', settings.prom_http_port)

    asyncio.run(_async_main(settings))
    logger.info('bias_monitor_exited')
