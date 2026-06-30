"""Synthetic-RCT label simulation for Day-2 T-learner training.

Per ADR 010: T ~ Bernoulli(0.5) independent of features. Outcomes Y are
simulated from the customer's embedded true response parameters plus the
row's current context (utilization, velocity_24h, paydown_rate_30d), using
the context-dependent response functions specified in docs/dgp_design.md § 5.

Profit accounting matches docs/01_problem_and_domain.md § "Cost-benefit per
action" with one correction: default loss applies ONLY when the offer is
accepted (because no new exposure exists otherwise). The cost-benefit
table simplifies away this conditional; the simulator does not.

All randomness flows through a caller-supplied `numpy.random.Generator` so
that seed determinism holds across runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np

# Economic constants — derived from docs/01_problem_and_domain.md
# Cost-benefit table. Captured here as Final so mypy enforces immutability
# and tests can assert against the same values that production uses.
NIM_ANNUAL: Final[float] = 0.12  # net interest margin on revolving balance
LGD: Final[float] = 0.80  # loss given default
DISCOUNT_FACTOR: Final[float] = 0.95  # 1-year horizon
CAPITAL_COST_RATE: Final[float] = 0.0064  # 8% required capital × 8% cost-of-capital
EXPOSURE_CAP_USD: Final[float] = 5000.0  # max CLI increment per offer

# Treatment-assignment probability (synthetic RCT — ADR 010)
P_TREAT: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class CustomerParams:
    """The latent ground-truth response parameters carried by every synthetic
    customer (see services/transactions/src/transactions/customer.py).

    These are what the T-learner is trying to recover from observable
    features alone. The label simulator uses them directly because at
    training time we *do* know them (it's synthetic).
    """

    customer_id: str
    segment_id: int
    true_p_accept_cli: float
    true_delta_spend_monthly: float
    true_p_default: float
    credit_limit: float


@dataclass(frozen=True, slots=True)
class RowContext:
    """The observable context at a given decision timestamp.

    Comes from a row of the joined per-window feature MVs at the same
    `window_end` bucket. Only the three features that drive the
    context-dependent response functions (per dgp_design.md § 5) are needed
    by the simulator; the rest of the feature vector is passed through to
    the model unchanged.
    """

    utilization: float
    velocity_24h: float
    paydown_rate_30d: float


@dataclass(frozen=True, slots=True)
class SimulatedLabel:
    """The Y portion of a training row. T is alongside but separate
    because downstream T-learner training needs them as distinct columns.
    """

    treated: int  # 0 or 1, drawn iid Bernoulli(0.5)
    accepted: int  # 0 or 1; meaningful only if treated == 1
    spend_delta: float  # realized incremental monthly spend if accepted, else 0
    defaulted: int  # 0 or 1; default on the new exposure (if any)
    profit: float  # realized profit — the T-learner regression target


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def effective_p_accept(
    customer: CustomerParams,
    ctx: RowContext,
) -> float:
    """Context-dependent acceptance probability (dgp_design.md § 5).

    Higher utilization → needs credit → more likely to accept.
    Higher recent velocity → spending actively → more likely to accept.
    Bounded to [0.01, 0.95] so neither arm collapses entirely.
    """
    util_boost = 0.3 * (ctx.utilization - 0.5)
    velocity_boost = 0.1 * min(ctx.velocity_24h / 20.0, 1.0)
    return _clip(customer.true_p_accept_cli + util_boost + velocity_boost, 0.01, 0.95)


def effective_p_default(
    customer: CustomerParams,
    ctx: RowContext,
) -> float:
    """Context-dependent default probability (dgp_design.md § 5).

    Higher utilization → financial strain → higher default risk.
    Lower paydown rate → revolving forever → higher default risk.
    Bounded to [0.001, 0.60].
    """
    util_risk = 0.2 * max(ctx.utilization - 0.6, 0.0)
    paydown_risk = 0.1 * max(0.5 - ctx.paydown_rate_30d, 0.0)
    return _clip(customer.true_p_default + util_risk + paydown_risk, 0.001, 0.60)


def _exposure(customer: CustomerParams, ctx: RowContext) -> float:
    """Available exposure granted by a CLI offer.

    Capped at EXPOSURE_CAP_USD to match the cost-benefit table assumption.
    Computed as `credit_limit * (1 - utilization)` to reflect the
    head-room being increased rather than the absolute new limit.
    """
    available = customer.credit_limit * (1.0 - ctx.utilization)
    return min(max(available, 0.0), EXPOSURE_CAP_USD)


def expected_profit_if_treated(
    customer: CustomerParams,
    ctx: RowContext,
) -> float:
    """Analytical expected profit of OFFER_CLI for (customer, context).

    Used both as the regression target's expected value for tests
    (Monte-Carlo realized profit should converge here) and, conceptually,
    as what a perfectly-calibrated uplift model would predict.

    profit = p_accept * [ annual_revenue * df - p_default * exposure * LGD
                          - capital_cost ]

    The accept × default conditioning matters: there's no default loss on
    exposure that was never extended.
    """
    p_accept = effective_p_accept(customer, ctx)
    p_default = effective_p_default(customer, ctx)
    exposure = _exposure(customer, ctx)

    annual_revenue = customer.true_delta_spend_monthly * 12 * NIM_ANNUAL
    discounted_revenue = annual_revenue * DISCOUNT_FACTOR
    expected_loss = p_default * exposure * LGD
    capital_cost = exposure * CAPITAL_COST_RATE

    profit_if_accept = discounted_revenue - expected_loss - capital_cost
    return p_accept * profit_if_accept


def simulate_label(
    customer: CustomerParams,
    ctx: RowContext,
    rng: np.random.Generator,
) -> SimulatedLabel:
    """Simulate one (T, Y) tuple per ADR 010 (synthetic RCT)."""
    treated = int(rng.binomial(1, P_TREAT))

    if treated == 0:
        # Control arm: nothing happens. Y baseline is 0 (we measure profit
        # *over* the do-nothing baseline, so the baseline itself is zero).
        return SimulatedLabel(
            treated=0, accepted=0, spend_delta=0.0, defaulted=0, profit=0.0
        )

    p_accept = effective_p_accept(customer, ctx)
    accepted = int(rng.binomial(1, p_accept))

    if accepted == 0:
        # Offer was made but declined. No incremental anything.
        return SimulatedLabel(
            treated=1, accepted=0, spend_delta=0.0, defaulted=0, profit=0.0
        )

    # Accepted — realize the incremental spend and simulate default on the
    # new exposure.
    spend_delta = customer.true_delta_spend_monthly
    exposure = _exposure(customer, ctx)
    p_default = effective_p_default(customer, ctx)
    defaulted = int(rng.binomial(1, p_default))

    annual_revenue = spend_delta * 12 * NIM_ANNUAL
    discounted_revenue = annual_revenue * DISCOUNT_FACTOR
    realized_loss = exposure * LGD if defaulted else 0.0
    capital_cost = exposure * CAPITAL_COST_RATE
    profit = discounted_revenue - realized_loss - capital_cost

    return SimulatedLabel(
        treated=1,
        accepted=accepted,
        spend_delta=spend_delta,
        defaulted=defaulted,
        profit=profit,
    )
