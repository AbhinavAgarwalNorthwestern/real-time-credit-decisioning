"""Unit tests for the heap-based merged-Poisson event generator.

Focused coverage of the 5 customer-level attributes denormalized onto every
Event: they must be present on the dataclass, round-trip through to_dict(),
pass validate(), and remain constant for the same customer across events.

All tests use in-process synthetic cohorts; no Kafka, no cluster, no cloud.
"""

from __future__ import annotations

from collections import defaultdict

from transactions.customer import generate_cohort
from transactions.generator import Event, TransactionGenerator


def _make_generator(cohort_size: int = 20, gen_seed: int = 99) -> TransactionGenerator:
    cohort = generate_cohort(size=cohort_size, seed=42)
    return TransactionGenerator(cohort=cohort, seed=gen_seed)


def test_event_has_5_new_fields() -> None:
    gen = _make_generator()
    e = gen.next_event()
    for fld in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert hasattr(e, fld), f'Event is missing field: {fld}'


def test_to_dict_includes_new_fields() -> None:
    """RisingWave ingestion needs every field in the JSON payload."""
    gen = _make_generator()
    e = gen.next_event()
    d = e.to_dict()
    for fld in (
        'credit_score',
        'annual_income',
        'account_tenure_months',
        'n_products',
        'prev_delinquency_count',
    ):
        assert fld in d, f'to_dict() missing: {fld}'


def test_validate_accepts_all_events() -> None:
    """Every event from a healthy cohort must pass validate()."""
    gen = _make_generator(cohort_size=30)
    for _ in range(200):
        e = gen.next_event()
        e.validate()  # raises on failure


def test_attributes_constant_per_customer() -> None:
    """The 5 attributes are constant per customer across every event they emit."""
    gen = _make_generator(cohort_size=20)
    events = [gen.next_event() for _ in range(500)]

    by_cust: defaultdict[str, list[Event]] = defaultdict(list)
    for e in events:
        by_cust[e.customer_id].append(e)

    for cust_id, evts in by_cust.items():
        if len(evts) < 2:
            continue
        first = evts[0]
        ref = (
            first.credit_score,
            first.annual_income,
            first.account_tenure_months,
            first.n_products,
            first.prev_delinquency_count,
        )
        for e in evts[1:]:
            current = (
                e.credit_score,
                e.annual_income,
                e.account_tenure_months,
                e.n_products,
                e.prev_delinquency_count,
            )
            assert current == ref, (
                f'{cust_id} inconsistent attributes across events: {current} != {ref}'
            )


def test_event_attributes_match_customer() -> None:
    """An emitted Event must mirror its source Customer's attributes."""
    cohort = generate_cohort(size=10, seed=42)
    gen = TransactionGenerator(cohort=cohort, seed=99)
    events = [gen.next_event() for _ in range(200)]

    cust_by_id = {c.customer_id: c for c in cohort}
    seen_matches = 0
    for e in events:
        c = cust_by_id[e.customer_id]
        assert e.credit_score == c.credit_score
        assert e.annual_income == c.annual_income
        assert e.account_tenure_months == c.account_tenure_months
        assert e.n_products == c.n_products
        assert e.prev_delinquency_count == c.prev_delinquency_count
        seen_matches += 1
    assert seen_matches > 0


def test_distinct_attributes_across_customers() -> None:
    """A diverse cohort must produce variation in the attributes (no cross-contamination)."""
    gen = _make_generator(cohort_size=50)
    events = [gen.next_event() for _ in range(500)]
    distinct_scores = {e.credit_score for e in events}
    distinct_incomes = {e.annual_income for e in events}
    # Different customers should have different attribute values
    assert len(distinct_scores) > 10
    assert len(distinct_incomes) > 10


def test_seed_determinism_generator() -> None:
    """Same cohort + same generator seed → identical event stream."""
    cohort = generate_cohort(size=10, seed=42)
    g1 = TransactionGenerator(cohort=cohort, seed=999)
    g2 = TransactionGenerator(cohort=cohort, seed=999)
    for _ in range(50):
        e1 = g1.next_event()
        e2 = g2.next_event()
        # Compare the customer-attribute payload (timestamps may differ by ms
        # because the heap initialization uses datetime.now() — that's separately
        # tested in the generator's existing tests).
        assert e1.customer_id == e2.customer_id
        assert e1.credit_score == e2.credit_score
        assert e1.annual_income == e2.annual_income
