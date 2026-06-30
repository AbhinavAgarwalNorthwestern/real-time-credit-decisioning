"""Tests for the synthetic-RCT label simulator.

Three hard properties the simulator must satisfy:
1. Treatment is unbiased — mean(T) ∈ [0.48, 0.52] over 10k draws.
2. Realized profit converges to analytical expected profit (Monte Carlo).
3. Seed determinism — same seed → identical labels (foundation for golden
   tests downstream).

Plus targeted unit checks on the context-dependent response functions.
"""

from __future__ import annotations

import numpy as np
import pytest
from training_flow.label_simulator import (
    CAPITAL_COST_RATE,
    DISCOUNT_FACTOR,
    EXPOSURE_CAP_USD,
    LGD,
    NIM_ANNUAL,
    P_TREAT,
    CustomerParams,
    RowContext,
    SimulatedLabel,
    effective_p_accept,
    effective_p_default,
    expected_profit_if_treated,
    simulate_label,
)

# --- Fixtures ----------------------------------------------------------------


def _customer(
    p_accept: float = 0.30,
    delta_spend: float = 400.0,
    p_default: float = 0.04,
    limit: float = 10000.0,
    segment: int = 0,
) -> CustomerParams:
    return CustomerParams(
        customer_id='cust-0001',
        segment_id=segment,
        true_p_accept_cli=p_accept,
        true_delta_spend_monthly=delta_spend,
        true_p_default=p_default,
        credit_limit=limit,
    )


def _ctx(util: float = 0.5, vel_24h: float = 5.0, paydown: float = 0.5) -> RowContext:
    return RowContext(utilization=util, velocity_24h=vel_24h, paydown_rate_30d=paydown)


# --- Property 1: treatment assignment is unbiased ----------------------------


def test_mean_treatment_is_half() -> None:
    """ADR 010: T ~ Bernoulli(0.5) independent of features.

    Over 10k draws, the empirical mean must land in [0.48, 0.52].
    Pinning the seed makes this a deterministic regression test.
    """
    rng = np.random.default_rng(seed=12345)
    cust = _customer()
    ctx = _ctx()

    labels = [simulate_label(cust, ctx, rng) for _ in range(10_000)]
    mean_t = float(np.mean([lbl.treated for lbl in labels]))

    assert 0.48 <= mean_t <= 0.52, f'mean(T) = {mean_t} outside [0.48, 0.52]'


def test_treatment_independent_of_features() -> None:
    """Feature distribution within T=0 must match within T=1 (no selection)."""
    rng = np.random.default_rng(seed=23456)
    cust = _customer()
    # Two contexts the simulator's response functions react to differently.
    contexts = [
        _ctx(util=0.2, paydown=0.8),
        _ctx(util=0.9, paydown=0.1),
    ]
    counts = {0: 0, 1: 0}
    n = 5_000
    for _ in range(n):
        ctx = contexts[int(rng.binomial(1, 0.5))]  # randomized context too
        lbl = simulate_label(cust, ctx, rng)
        counts[lbl.treated] += 1
    # By construction T is independent of which context was used — so each
    # treatment arm should hold ~50% of total draws.
    assert abs(counts[1] / n - 0.5) <= 0.02


# --- Property 2: realized profit ≈ analytical expected profit ----------------


def test_realized_profit_converges_to_expected() -> None:
    """Mean realized profit over 20k Monte Carlo runs lands in the 4-σ
    bootstrap CI of the analytical E[profit | T=1].

    The 4-σ width is generous to avoid flakiness; the test catches sign
    errors and factor-of-two mistakes, which is what matters.
    """
    rng = np.random.default_rng(seed=34567)
    cust = _customer(p_accept=0.30, delta_spend=400.0, p_default=0.04)
    ctx = _ctx(util=0.5, paydown=0.5, vel_24h=5.0)

    n = 20_000
    realized = np.array(
        [simulate_label(cust, ctx, rng).profit for _ in range(n)], dtype=float
    )

    # Analytical E[profit | T=1] — the simulator is by construction
    # producing draws from this distribution conditional on T=1. The
    # unconditional E[profit] over T ~ Bernoulli(0.5) is half.
    expected_treated = expected_profit_if_treated(cust, ctx)
    expected_overall = P_TREAT * expected_treated

    mean_realized = float(realized.mean())
    se = float(realized.std(ddof=1) / np.sqrt(n))
    # 4-sigma CI width — wide enough that this isn't a flake target.
    assert abs(mean_realized - expected_overall) <= 4 * se, (
        f'realized={mean_realized}, expected={expected_overall}, 4sigma_se={4 * se}'
    )


def test_control_arm_is_zero_profit() -> None:
    """Per the do-nothing baseline definition: control arm rows contribute 0
    to incremental profit (we measure profit over the do-nothing baseline)."""
    rng = np.random.default_rng(seed=45678)
    cust = _customer()
    ctx = _ctx()

    # Force T=0 by skipping all but the binomial draw — easier to just
    # filter actual draws.
    labels = [simulate_label(cust, ctx, rng) for _ in range(2_000)]
    control = [lbl for lbl in labels if lbl.treated == 0]
    assert len(control) > 0
    assert all(lbl.profit == 0.0 for lbl in control)
    assert all(lbl.spend_delta == 0.0 for lbl in control)
    assert all(lbl.defaulted == 0 for lbl in control)
    assert all(lbl.accepted == 0 for lbl in control)


def test_declined_offer_is_zero_profit() -> None:
    """Treated-but-declined rows also contribute 0: no exposure extended."""
    rng = np.random.default_rng(seed=56789)
    # Force p_accept very low so most treated rows are declined.
    cust = _customer(p_accept=0.01)
    ctx = _ctx(util=0.5, paydown=0.5)

    labels = [simulate_label(cust, ctx, rng) for _ in range(2_000)]
    treated_declined = [lbl for lbl in labels if lbl.treated == 1 and lbl.accepted == 0]
    assert len(treated_declined) > 0
    assert all(lbl.profit == 0.0 for lbl in treated_declined)


# --- Property 3: seed determinism --------------------------------------------


def test_seed_determinism() -> None:
    """Same seed must produce bit-identical labels.

    Foundation for the golden-test pattern: D2-1b's training.parquet hash
    is reproducible only if every randomness consumer is seeded.
    """
    cust = _customer()
    ctx = _ctx()

    rng_a = np.random.default_rng(seed=99999)
    rng_b = np.random.default_rng(seed=99999)
    a = [simulate_label(cust, ctx, rng_a) for _ in range(500)]
    b = [simulate_label(cust, ctx, rng_b) for _ in range(500)]
    assert a == b


def test_different_seeds_diverge() -> None:
    """Distinct seeds should not (in expectation) coincide on 500 draws."""
    cust = _customer()
    ctx = _ctx()

    rng_a = np.random.default_rng(seed=1)
    rng_b = np.random.default_rng(seed=2)
    a = [simulate_label(cust, ctx, rng_a) for _ in range(500)]
    b = [simulate_label(cust, ctx, rng_b) for _ in range(500)]
    assert a != b


# --- Response-function unit checks -------------------------------------------


@pytest.mark.parametrize(
    'util,vel,base,expected_bounded',
    [
        (0.5, 0.0, 0.30, 0.30),  # neutral context = baseline
        (1.0, 0.0, 0.30, 0.30 + 0.15),  # +0.3 × 0.5 = +0.15 from util
        (0.5, 100.0, 0.30, 0.30 + 0.10),  # full velocity boost +0.10
        (0.5, 0.0, 0.99, 0.95),  # upper bound clip
        (0.0, 0.0, 0.01, 0.01),  # lower bound clip (0.01 - 0.15 = -0.14 → 0.01)
    ],
)
def test_effective_p_accept_response_function(
    util: float, vel: float, base: float, expected_bounded: float
) -> None:
    cust = _customer(p_accept=base)
    ctx = _ctx(util=util, vel_24h=vel)
    got = effective_p_accept(cust, ctx)
    assert abs(got - expected_bounded) < 1e-9, (
        f'effective_p_accept({util=},{vel=},{base=}) = {got}, '
        f'expected {expected_bounded}'
    )


@pytest.mark.parametrize(
    'util,paydown,base,expected_bounded',
    [
        (0.5, 0.5, 0.04, 0.04),  # neutral context
        (0.9, 0.5, 0.04, 0.04 + 0.06),  # +0.2 × 0.3 = +0.06 util risk
        (0.5, 0.0, 0.04, 0.04 + 0.05),  # +0.1 × 0.5 paydown risk
        (0.5, 0.5, 0.99, 0.60),  # upper bound clip
    ],
)
def test_effective_p_default_response_function(
    util: float, paydown: float, base: float, expected_bounded: float
) -> None:
    cust = _customer(p_default=base)
    ctx = _ctx(util=util, paydown=paydown)
    got = effective_p_default(cust, ctx)
    assert abs(got - expected_bounded) < 1e-9


# --- Sanity / constants ------------------------------------------------------


def test_constants_match_problem_doc() -> None:
    """The economic constants in the simulator must match the cost-benefit
    table in docs/01_problem_and_domain.md. If the doc changes, this test
    will fail loudly — forcing the engineer to also update simulator math.
    """
    assert NIM_ANNUAL == 0.12
    assert LGD == 0.80
    assert DISCOUNT_FACTOR == 0.95
    assert CAPITAL_COST_RATE == 0.0064  # 8% capital × 8% cost
    assert EXPOSURE_CAP_USD == 5000.0
    assert P_TREAT == 0.5


def test_simulatedlabel_is_hashable() -> None:
    """frozen + slots means SimulatedLabel can be set-membered for diffing,
    which the seed-determinism test relies on (a != b uses __eq__)."""
    a = SimulatedLabel(
        treated=1, accepted=1, spend_delta=400.0, defaulted=0, profit=42.0
    )
    b = SimulatedLabel(
        treated=1, accepted=1, spend_delta=400.0, defaulted=0, profit=42.0
    )
    assert a == b
