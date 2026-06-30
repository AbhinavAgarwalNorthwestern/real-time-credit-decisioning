"""Integration tests for producer state checkpoint save/load and generator restore.

The S3 I/O is tested via moto (in-memory S3 mock). The generator integration
is tested without any I/O — just verifying that injecting `restore_from`
overrides baseline_balance correctly.

Note: marked integration because moto 5.x does not intercept custom endpoint_url
(the state_checkpoint module passes endpoint_url to boto3 client).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

boto3 = pytest.importorskip('boto3')
moto = pytest.importorskip('moto')
from moto import mock_aws  # noqa: E402
from transactions.customer import generate_cohort  # noqa: E402
from transactions.generator import TransactionGenerator, _CustomerState  # noqa: E402
from transactions.state_checkpoint import (  # noqa: E402
    CHECKPOINT_VERSION,
    CustomerStateSnapshot,
    load_states,
    save_states,
)

_BUCKET = 'transactions-state'
_KEY = 'customer_states.json'
_ENDPOINT = 'http://localhost:9000'  # moto intercepts; endpoint is symbolic


def _snapshots_for(
    cohort, balances: dict[str, float] | None = None
) -> list[CustomerStateSnapshot]:
    """Build snapshots from a cohort, optionally overriding balances per id."""
    bal = balances or {}
    return [
        CustomerStateSnapshot(
            customer_id=c.customer_id,
            balance=bal.get(
                c.customer_id, c.baseline_balance * 2.0
            ),  # default: 2x baseline
            session_end_ms=0,
            session_factor=1.0,
        )
        for c in cohort
    ]


@mock_aws
def test_save_load_round_trip_matches() -> None:
    """save_states(snaps) then load_states() returns identical data."""
    cohort = generate_cohort(size=10, seed=42)
    snaps = _snapshots_for(cohort, balances={cohort[0].customer_id: 4242.42})

    save_states(
        snaps,
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        seed=42,
        source_mode='backfill',
    )
    loaded = load_states(
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        expected_cohort_size=10,
        expected_seed=42,
    )
    assert loaded is not None
    assert len(loaded) == 10
    by_id = {s.customer_id: s for s in loaded}
    assert by_id[cohort[0].customer_id].balance == 4242.42


@mock_aws
def test_load_returns_none_when_object_missing() -> None:
    """First-run case: no checkpoint exists yet → graceful None."""
    # Make the bucket but not the object
    client = boto3.client(
        's3',
        endpoint_url=_ENDPOINT,
        aws_access_key_id='x',
        aws_secret_access_key='x',
        region_name='us-east-1',
    )
    client.create_bucket(Bucket=_BUCKET)

    result = load_states(
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key='does-not-exist.json',
        expected_cohort_size=10,
        expected_seed=42,
    )
    assert result is None


@mock_aws
def test_load_returns_none_on_seed_mismatch() -> None:
    """Different seed cohorts have different customer IDs → don't apply old balances."""
    cohort = generate_cohort(size=10, seed=42)
    snaps = _snapshots_for(cohort)

    save_states(
        snaps,
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        seed=42,
        source_mode='backfill',
    )
    # Try to load with a different expected seed
    result = load_states(
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        expected_cohort_size=10,
        expected_seed=999,
    )
    assert result is None


@mock_aws
def test_load_returns_none_on_cohort_size_mismatch() -> None:
    """Different cohort size means different customer set → discard."""
    cohort_small = generate_cohort(size=10, seed=42)
    snaps = _snapshots_for(cohort_small)
    save_states(
        snaps,
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        seed=42,
        source_mode='backfill',
    )
    # Expect a 100-customer cohort but file has 10
    result = load_states(
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        expected_cohort_size=100,
        expected_seed=42,
    )
    assert result is None


@mock_aws
def test_generator_uses_restored_balances() -> None:
    """When restore_from is provided, the generator's internal states reflect it."""
    cohort = generate_cohort(size=5, seed=42)
    custom_balance = 9999.99
    snaps = [
        CustomerStateSnapshot(
            customer_id=c.customer_id,
            balance=custom_balance,
            session_end_ms=0,
            session_factor=1.0,
        )
        for c in cohort
    ]
    gen = TransactionGenerator(cohort=cohort, seed=99, restore_from=snaps)
    # Inspect internal state — every customer should have the custom balance
    # rather than baseline_balance
    for i, c in enumerate(cohort):
        state: _CustomerState = gen._states[i]
        assert state.balance == custom_balance, (
            f'expected custom balance for {c.customer_id}, got {state.balance}'
        )


def test_generator_falls_back_to_baseline_when_no_restore() -> None:
    """Without restore_from, generator uses baseline_balance (legacy behavior)."""
    cohort = generate_cohort(size=5, seed=42)
    gen = TransactionGenerator(cohort=cohort, seed=99)
    for i, c in enumerate(cohort):
        assert gen._states[i].balance == c.baseline_balance


def test_generator_falls_back_to_baseline_for_unknown_customer() -> None:
    """If checkpoint has a partial customer set, missing customers fall back to baseline."""
    cohort = generate_cohort(size=5, seed=42)
    # Only restore first customer
    snaps = [
        CustomerStateSnapshot(
            customer_id=cohort[0].customer_id,
            balance=1234.56,
            session_end_ms=0,
            session_factor=1.0,
        )
    ]
    gen = TransactionGenerator(cohort=cohort, seed=99, restore_from=snaps)
    assert gen._states[0].balance == 1234.56
    for i in range(1, 5):
        assert gen._states[i].balance == cohort[i].baseline_balance


@mock_aws
def test_snapshot_states_returns_current_state() -> None:
    """generator.snapshot_states() captures live state for checkpointing."""
    cohort = generate_cohort(size=3, seed=42)
    gen = TransactionGenerator(cohort=cohort, seed=99)

    # Mutate internal state to simulate live activity
    gen._states[0].balance = 555.5
    gen._states[1].balance = 888.8
    gen._states[2].balance = 1111.1
    gen._states[0].session_end_ms = 1234567890
    gen._states[0].session_factor = 3.0

    snaps = gen.snapshot_states()
    assert len(snaps) == 3
    assert snaps[0].customer_id == cohort[0].customer_id
    assert snaps[0].balance == 555.5
    assert snaps[0].session_end_ms == 1234567890
    assert snaps[0].session_factor == 3.0
    assert snaps[1].balance == 888.8
    assert snaps[2].balance == 1111.1


@mock_aws
def test_round_trip_via_generator_snapshot() -> None:
    """End-to-end: generator → snapshot_states → save → load → restore → verify."""
    cohort = generate_cohort(size=5, seed=42)
    gen1 = TransactionGenerator(cohort=cohort, seed=99)
    # Simulate accumulated balance
    for i in range(5):
        gen1._states[i].balance = 1000.0 + i

    snaps = gen1.snapshot_states()
    save_states(
        snaps,
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        seed=42,
        source_mode='live',
    )

    loaded = load_states(
        endpoint=_ENDPOINT,
        access_key='x',
        secret_key='x',
        bucket=_BUCKET,
        object_key=_KEY,
        expected_cohort_size=5,
        expected_seed=42,
    )
    assert loaded is not None

    gen2 = TransactionGenerator(cohort=cohort, seed=99, restore_from=loaded)
    for i in range(5):
        assert gen2._states[i].balance == 1000.0 + i


def test_checkpoint_version_constant() -> None:
    """Checkpoint format is versioned for forward compatibility."""
    assert isinstance(CHECKPOINT_VERSION, int)
    assert CHECKPOINT_VERSION >= 1
