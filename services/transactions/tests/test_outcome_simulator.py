"""Unit tests for outcome_simulator.

Tests verify that:
  1. NOTHING + FRAUD_CHECK actions yield zero realized profit
  2. OFFER_CLI accepted+nondefault → positive profit
  3. OFFER_CLI accepted+default → negative profit (default loss > NIM revenue)
  4. effective_p_accept / effective_p_default match the dgp_design.md formulas
  5. Aggregate over many simulations matches expected probabilities
  6. Outcomes are serializable to Kafka JSON without information loss
"""

from __future__ import annotations

import json
import random

import pytest
from transactions.outcome_simulator import (
    DISCOUNT_FACTOR,
    LGD,
    NIM_ANNUAL,
    Decision,
    GroundTruthParams,
    Outcome,
    effective_p_accept,
    effective_p_default,
    simulate_outcome,
)


def _make_decision(action: str = 'OFFER_CLI', utilization: float = 0.5) -> Decision:
    return Decision(
        decision_id='d-001',
        customer_id='cust-000001',
        timestamp_ms=1_700_000_000_000,
        action=action,
        alias='champion',
        segment_id=0,
        current_utilization=utilization,
        velocity_24h=5.0,
        paydown_rate_30d=0.5,
    )


def _make_params(
    p_accept: float = 0.3, delta_spend: float = 400.0, p_default: float = 0.05
) -> GroundTruthParams:
    return GroundTruthParams(
        customer_id='cust-000001',
        segment_id=0,
        true_p_accept_cli=p_accept,
        true_delta_spend_if_accept=delta_spend,
        true_p_default=p_default,
    )


class TestEffectiveProbabilities:
    """Per dgp_design.md §5 — context-dependent response functions."""

    def test_p_accept_at_baseline_utilization(self) -> None:
        # At utilization=0.5, util_boost is exactly 0
        # velocity_24h=0 → velocity_boost=0
        # So effective_p_accept should equal base
        p = effective_p_accept(
            base_p_accept=0.3, current_utilization=0.5, velocity_24h=0.0
        )
        assert p == pytest.approx(0.3, abs=1e-6)

    def test_p_accept_increases_with_utilization(self) -> None:
        # High utilization → wants credit → higher p_accept
        low = effective_p_accept(0.3, current_utilization=0.2, velocity_24h=None)
        high = effective_p_accept(0.3, current_utilization=0.8, velocity_24h=None)
        assert high > low

    def test_p_accept_increases_with_velocity(self) -> None:
        # High velocity = actively spending = more likely to want credit
        low = effective_p_accept(0.3, current_utilization=0.5, velocity_24h=0.0)
        high = effective_p_accept(0.3, current_utilization=0.5, velocity_24h=20.0)
        assert high > low

    def test_p_accept_clipped_to_valid_range(self) -> None:
        # Edge case: base + boosts could exceed 1.0; should clip to 0.95
        p = effective_p_accept(0.95, current_utilization=0.95, velocity_24h=100.0)
        assert 0.0 < p <= 0.95

        # Edge case: very low base + low context could go below 0; clip to 0.01
        p = effective_p_accept(0.01, current_utilization=0.0, velocity_24h=0.0)
        assert 0.01 <= p < 1.0

    def test_p_default_no_risk_at_low_utilization(self) -> None:
        # util < 0.6 → util_risk = 0
        # paydown >= 0.5 → paydown_risk = 0
        # So effective_p_default == base
        p = effective_p_default(
            base_p_default=0.05, current_utilization=0.4, paydown_rate_30d=0.7
        )
        assert p == pytest.approx(0.05, abs=1e-6)

    def test_p_default_increases_with_utilization(self) -> None:
        low = effective_p_default(0.05, current_utilization=0.4, paydown_rate_30d=0.5)
        high = effective_p_default(0.05, current_utilization=0.9, paydown_rate_30d=0.5)
        assert high > low

    def test_p_default_increases_with_low_paydown(self) -> None:
        # Low paydown rate = poor repayment behavior = higher default risk
        high_paydown = effective_p_default(
            0.05, current_utilization=0.5, paydown_rate_30d=0.9
        )
        low_paydown = effective_p_default(
            0.05, current_utilization=0.5, paydown_rate_30d=0.1
        )
        assert low_paydown > high_paydown


class TestSimulateOutcome:
    """Outcome simulation per action type and probabilistic correctness."""

    def test_nothing_action_yields_zero_profit(self) -> None:
        rng = random.Random(42)
        decision = _make_decision(action='NOTHING')
        params = _make_params()
        outcome = simulate_outcome(decision, params, rng)

        assert outcome.accepted is False
        assert outcome.defaulted is False
        assert outcome.realized_profit_usd == 0.0
        assert outcome.realized_spend_usd == 0.0
        assert outcome.exposure_usd == 0.0

    def test_fraud_check_action_yields_zero_profit(self) -> None:
        rng = random.Random(42)
        decision = _make_decision(action='FRAUD_CHECK')
        params = _make_params()
        outcome = simulate_outcome(decision, params, rng)

        assert outcome.accepted is False
        assert outcome.realized_profit_usd == 0.0

    def test_offer_cli_accepted_nondefault_yields_positive_profit(self) -> None:
        # Force p_accept high enough to always accept; p_default low enough to never default
        rng = random.Random(42)
        decision = _make_decision(action='OFFER_CLI', utilization=0.5)
        params = _make_params(p_accept=0.95, delta_spend=500.0, p_default=0.001)

        # Run several to ensure at least one passes both Bernoullis
        accepted_outcomes = []
        for _ in range(50):
            outcome = simulate_outcome(decision, params, rng)
            if outcome.accepted and not outcome.defaulted:
                accepted_outcomes.append(outcome)

        assert len(accepted_outcomes) >= 30  # high p_accept × low p_default → many

        for outcome in accepted_outcomes:
            assert outcome.realized_profit_usd > 0
            assert outcome.realized_spend_usd > 0
            assert outcome.exposure_usd > 0
            assert outcome.exposure_usd <= 5000.0  # capped

    def test_offer_cli_accepted_default_yields_negative_profit(self) -> None:
        # Force p_default very high so defaults dominate
        rng = random.Random(42)
        decision = _make_decision(action='OFFER_CLI', utilization=0.7)
        params = _make_params(p_accept=0.99, delta_spend=300.0, p_default=0.95)

        defaulted_outcomes = []
        for _ in range(50):
            outcome = simulate_outcome(decision, params, rng)
            if outcome.accepted and outcome.defaulted:
                defaulted_outcomes.append(outcome)

        assert len(defaulted_outcomes) >= 20

        # When defaulted: default_loss (exposure × LGD) > NIM revenue
        for outcome in defaulted_outcomes:
            assert outcome.realized_profit_usd < 0
            assert outcome.exposure_usd > 0

    def test_aggregate_acceptance_rate_matches_expected(self) -> None:
        """Over many trials, P(accepted | OFFER_CLI) ≈ effective_p_accept."""
        rng = random.Random(123)
        decision = _make_decision(action='OFFER_CLI', utilization=0.5)
        params = _make_params(p_accept=0.40, delta_spend=300.0, p_default=0.05)

        n_trials = 5000
        accepted = 0
        for _ in range(n_trials):
            outcome = simulate_outcome(decision, params, rng)
            if outcome.accepted:
                accepted += 1

        # Expected: effective_p_accept(0.40, 0.5, 5.0) = 0.40 + 0 + 0.025 = 0.425
        rate = accepted / n_trials
        assert rate == pytest.approx(0.425, abs=0.02)

    def test_aggregate_default_rate_matches_expected(self) -> None:
        """Over many accepted trials, P(default | accepted) ≈ effective_p_default."""
        rng = random.Random(123)
        decision = _make_decision(action='OFFER_CLI', utilization=0.7)
        params = _make_params(p_accept=0.95, delta_spend=300.0, p_default=0.10)

        n_trials = 5000
        accepted_count = 0
        defaulted_count = 0
        for _ in range(n_trials):
            outcome = simulate_outcome(decision, params, rng)
            if outcome.accepted:
                accepted_count += 1
                if outcome.defaulted:
                    defaulted_count += 1

        # Expected: effective_p_default(0.10, 0.7, 0.5) = 0.10 + 0.2×0.1 + 0 = 0.12
        default_rate = defaulted_count / accepted_count if accepted_count else 0
        assert default_rate == pytest.approx(0.12, abs=0.02)


class TestOutcomeSerialization:
    """Outcome → Kafka JSON round-trip."""

    def test_to_kafka_json_is_valid_json(self) -> None:
        outcome = Outcome(
            decision_id='d-001',
            customer_id='cust-001',
            decision_timestamp_ms=1_700_000_000_000,
            outcome_timestamp_ms=1_700_000_030_000,
            accepted=True,
            defaulted=False,
            realized_spend_usd=523.45,
            realized_profit_usd=68.21,
            exposure_usd=4500.00,
            latency_to_outcome_ms=30_000,
        )
        raw = outcome.to_kafka_json()
        decoded = json.loads(raw.decode())

        assert decoded['decision_id'] == 'd-001'
        assert decoded['customer_id'] == 'cust-001'
        assert decoded['accepted'] is True
        assert decoded['defaulted'] is False
        assert decoded['realized_profit_usd'] == 68.21
        assert decoded['exposure_usd'] == 4500.00
        assert decoded['latency_to_outcome_ms'] == 30_000

    def test_to_kafka_json_compact_encoding(self) -> None:
        """JSON should use compact separators (no whitespace) for Kafka size."""
        outcome = Outcome(
            decision_id='d-001',
            customer_id='cust-001',
            decision_timestamp_ms=1_700_000_000_000,
            outcome_timestamp_ms=1_700_000_030_000,
            accepted=False,
            defaulted=False,
            realized_spend_usd=0.0,
            realized_profit_usd=0.0,
            exposure_usd=0.0,
            latency_to_outcome_ms=30_000,
        )
        raw = outcome.to_kafka_json().decode()
        # Compact JSON has no spaces after , or :
        assert ', ' not in raw
        assert ': ' not in raw


class TestEconomicSanity:
    """Profit-equation sanity tests."""

    def test_nondefault_profit_equation(self) -> None:
        """Verify the profit equation matches the documented formula.

        On accept+nondefault: profit = spend × NIM × discount_factor - capital_cost
        where capital_cost = exposure × CAPITAL_COST_RATE
        """
        rng = random.Random(42)
        decision = _make_decision(action='OFFER_CLI', utilization=0.5)
        # No noise reachable, but spend has lognormal noise — so we'll allow a bound
        params = _make_params(p_accept=0.99, delta_spend=500.0, p_default=0.0001)

        outcomes = [simulate_outcome(decision, params, rng) for _ in range(100)]
        nondefault = [o for o in outcomes if o.accepted and not o.defaulted]
        assert len(nondefault) > 0

        for o in nondefault:
            # profit = spend × NIM × discount - exposure × capital_cost_rate
            expected = (
                o.realized_spend_usd * NIM_ANNUAL * DISCOUNT_FACTOR
                - o.exposure_usd * 0.08 * 0.08
            )
            assert o.realized_profit_usd == pytest.approx(expected, abs=0.01)

    def test_default_loss_dominates_nim_at_high_lgd(self) -> None:
        """When LGD=0.80 and exposure=5000, default loss = $4000 — should
        always exceed NIM revenue at typical spend levels."""
        rng = random.Random(42)
        decision = _make_decision(action='OFFER_CLI', utilization=0.9)
        params = _make_params(p_accept=0.99, delta_spend=500.0, p_default=0.95)

        outcomes = [simulate_outcome(decision, params, rng) for _ in range(100)]
        defaulted = [o for o in outcomes if o.accepted and o.defaulted]
        assert len(defaulted) > 0

        # On default: default_loss should be roughly exposure × LGD = exposure × 0.8
        for o in defaulted:
            # Realized profit should be negative
            assert o.realized_profit_usd < 0
            # Default loss component magnitude
            default_loss = o.exposure_usd * LGD
            # NIM revenue
            nim_revenue = o.realized_spend_usd * NIM_ANNUAL * DISCOUNT_FACTOR
            assert default_loss > nim_revenue  # the dominant term
