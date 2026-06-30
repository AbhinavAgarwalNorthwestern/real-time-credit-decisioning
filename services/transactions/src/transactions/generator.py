"""Synthetic transaction event generator — heap-based merged Poisson process.

Each customer is an independent non-homogeneous Poisson process with:
  - Base rate λ_i (Gamma-distributed, from customer.txn_rate)
  - Circadian modulation (time-of-day peaks, from segment)
  - Day-of-week modulation
  - Session bursts (temporary rate boost after an event)

The generator maintains a min-heap of (next_event_time, customer_index)
and always emits the earliest scheduled event. This produces a globally
monotonic timestamp stream required by RisingWave's watermark semantics.

The generator is stateful (tracks balance, session state) but fully
deterministic given the seed.

See docs/dgp_design.md for the complete DGP specification.
"""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import numpy as np

from transactions.customer import Customer
from transactions.distributions import (
    circadian_multiplier,
    dow_multiplier,
    perturb_geo,
    sample_amount,
    sample_mcc,
)
from transactions.segments import SEGMENT_BY_ID, SegmentId, SegmentParams
from transactions.state_checkpoint import CustomerStateSnapshot


@dataclass(frozen=True, slots=True)
class Event:
    """One synthetic transaction event.

    Schema matches `deployments/dev/risingwave/00_source_transactions.sql`.
    """

    event_id: str
    customer_id: str
    timestamp_ms: int
    amount: float
    mcc: int
    merchant_id: str
    geo_lat: float
    geo_lon: float
    is_card_present: bool
    current_balance: float
    credit_limit: float
    segment_id: int
    # Customer-level attributes denormalized onto every event (constant per
    # customer) so RisingWave can derive the customer_attributes MV from a
    # single source stream.
    credit_score: int
    annual_income: float
    account_tenure_months: int
    n_products: int
    prev_delinquency_count: int
    payload_version: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def validate(self) -> None:
        """Debug-mode assertions. Enable via TXN_VALIDATE=true."""
        assert self.amount > 0, f'amount must be positive: {self.amount}'
        assert 0 <= self.segment_id <= 5, f'segment_id out of range: {self.segment_id}'
        assert self.credit_limit > 0, (
            f'credit_limit must be positive: {self.credit_limit}'
        )
        assert self.current_balance >= 0, (
            f'balance cannot be negative: {self.current_balance}'
        )
        assert self.current_balance <= self.credit_limit * 1.01, (
            f'balance {self.current_balance} exceeds limit {self.credit_limit}'
        )
        assert -90 <= self.geo_lat <= 90, f'lat out of range: {self.geo_lat}'
        assert -180 <= self.geo_lon <= 180, f'lon out of range: {self.geo_lon}'
        assert self.timestamp_ms > 0, (
            f'timestamp_ms must be positive: {self.timestamp_ms}'
        )
        assert 300 <= self.credit_score <= 850, (
            f'credit_score out of range: {self.credit_score}'
        )
        assert self.annual_income > 0, (
            f'annual_income must be positive: {self.annual_income}'
        )
        assert 0 <= self.account_tenure_months <= 360, (
            f'account_tenure_months out of range: {self.account_tenure_months}'
        )
        assert 0 <= self.n_products <= 10, f'n_products out of range: {self.n_products}'
        assert 0 <= self.prev_delinquency_count <= 20, (
            f'prev_delinquency_count out of range: {self.prev_delinquency_count}'
        )


@dataclass(slots=True)
class _CustomerState:
    """Mutable per-customer state tracked by the generator."""

    balance: float
    session_end_ms: int = 0  # 0 means no active session
    session_factor: float = 1.0


class TransactionGenerator:
    """Heap-based merged Poisson process across the customer cohort."""

    def __init__(
        self,
        cohort: list[Customer],
        seed: int,
        restore_from: list[CustomerStateSnapshot] | None = None,
    ) -> None:
        if not cohort:
            raise ValueError('cohort must be non-empty')
        self._rng = np.random.default_rng(seed)
        self._cohort = cohort
        self._n_customers = len(cohort)

        # Per-customer mutable state. If a checkpoint was supplied, restore
        # each customer's balance + session state from it. Otherwise initialize
        # from the segment-derived baseline_balance. This is the heart of
        # producer state persistence — see state_checkpoint.py for the rationale.
        restore_by_id: dict[str, CustomerStateSnapshot] = (
            {s.customer_id: s for s in restore_from} if restore_from else {}
        )
        self._states: list[_CustomerState] = []
        for c in cohort:
            snap = restore_by_id.get(c.customer_id)
            if snap is not None:
                self._states.append(
                    _CustomerState(
                        balance=snap.balance,
                        session_end_ms=snap.session_end_ms,
                        session_factor=snap.session_factor,
                    )
                )
            else:
                self._states.append(_CustomerState(balance=c.baseline_balance))

        # Segment lookup cache
        self._segments: list[SegmentParams] = [
            SEGMENT_BY_ID[SegmentId(c.segment_id)] for c in cohort
        ]

        # Build the initial event heap: schedule first event for each customer
        # Heap entries: (next_event_time_ms, customer_index)
        self._heap: list[tuple[int, int]] = []
        start_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        for i, _customer in enumerate(cohort):
            wait_ms = self._sample_wait_ms(i, start_ms)
            heapq.heappush(self._heap, (start_ms + wait_ms, i))

    def _sample_wait_ms(self, cust_idx: int, current_ms: int) -> int:
        """Sample inter-arrival time for a customer, accounting for modulation."""
        customer = self._cohort[cust_idx]
        seg = self._segments[cust_idx]
        state = self._states[cust_idx]

        base_rate = customer.txn_rate

        # Circadian modulation
        dt = datetime.fromtimestamp(current_ms / 1000.0, tz=timezone.utc)
        hour = dt.hour + dt.minute / 60.0
        circ = circadian_multiplier(hour, seg)

        # Day-of-week modulation
        dow = dow_multiplier(dt.weekday())

        # Session boost
        session = state.session_factor if current_ms < state.session_end_ms else 1.0

        effective_rate = base_rate * circ * dow * session

        if effective_rate <= 0:
            effective_rate = 1e-6

        # Exponential inter-arrival (in seconds), convert to ms
        wait_s = float(self._rng.exponential(1.0 / effective_rate))
        return max(1, int(wait_s * 1000))

    def next_event(self, timestamp_ms: int | None = None) -> Event:
        """Produce the next event from the merged process.

        If timestamp_ms is None, uses the heap-scheduled time (normal mode).
        If timestamp_ms is provided (backfill/live override), uses that
        timestamp but still picks the customer from the heap ordering.
        """
        # Pop the earliest-scheduled customer
        scheduled_ms, cust_idx = heapq.heappop(self._heap)

        # Use provided timestamp or the scheduled one
        ts_ms = timestamp_ms if timestamp_ms is not None else scheduled_ms

        customer = self._cohort[cust_idx]
        seg = self._segments[cust_idx]
        state = self._states[cust_idx]

        # Sample event attributes
        mcc = sample_mcc(self._rng, customer.mcc_weights)
        amount = sample_amount(self._rng, seg, mcc)
        geo_lat, geo_lon = perturb_geo(
            customer.home_lat, customer.home_lon, self._rng, seg
        )

        # Update running balance (capped at credit limit)
        new_balance = min(customer.credit_limit, state.balance + amount)

        # Paydown check — segment-driven frequency
        if float(self._rng.random()) < customer.paydown_freq * 0.02:
            # paydown_freq is the "propensity" — actual event-level trigger is
            # scaled to ~1-3% per event for realistic paydown cadence
            paydown_fraction = float(self._rng.uniform(0.30, 0.80))
            new_balance *= 1.0 - paydown_fraction

        state.balance = new_balance

        # Card-present
        is_card_present = bool(float(self._rng.random()) < customer.card_present_rate)

        # Session burst trigger
        if float(self._rng.random()) < seg.p_session:
            state.session_end_ms = ts_ms + int(seg.session_duration_s * 1000)
            state.session_factor = seg.session_factor
        elif ts_ms >= state.session_end_ms:
            state.session_factor = 1.0

        # Schedule next event for this customer
        next_wait_ms = self._sample_wait_ms(cust_idx, ts_ms)
        heapq.heappush(self._heap, (ts_ms + next_wait_ms, cust_idx))

        return Event(
            event_id=str(uuid4()),
            customer_id=customer.customer_id,
            timestamp_ms=ts_ms,
            amount=amount,
            mcc=mcc,
            merchant_id=f'm-{int(self._rng.integers(1, 100000)):06d}',
            geo_lat=geo_lat,
            geo_lon=geo_lon,
            is_card_present=is_card_present,
            current_balance=round(new_balance, 2),
            credit_limit=round(customer.credit_limit, 2),
            segment_id=customer.segment_id,
            credit_score=customer.credit_score,
            annual_income=customer.annual_income,
            account_tenure_months=customer.account_tenure_months,
            n_products=customer.n_products,
            prev_delinquency_count=customer.prev_delinquency_count,
            payload_version=1,
        )

    def next_event_scheduled_time_ms(self) -> int:
        """Peek at the next scheduled event time without consuming it."""
        return self._heap[0][0]

    @property
    def cohort_size(self) -> int:
        return self._n_customers

    def snapshot_states(self) -> list[CustomerStateSnapshot]:
        """Capture per-customer state for checkpointing.

        Used by main.py periodic save + SIGTERM handler. The snapshot list
        is ordered the same as `self._cohort` (i.e., customer_id matches).
        """
        return [
            CustomerStateSnapshot(
                customer_id=self._cohort[i].customer_id,
                balance=self._states[i].balance,
                session_end_ms=self._states[i].session_end_ms,
                session_factor=self._states[i].session_factor,
            )
            for i in range(self._n_customers)
        ]
