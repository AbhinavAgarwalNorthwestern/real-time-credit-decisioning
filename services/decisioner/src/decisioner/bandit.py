"""Day-3: Linear contextual Thompson Sampling bandit.

Three arms: OFFER_CLI, FRAUD_CHECK, NOTHING. Per ADR/decision-doc
section 2 in docs/03_models_and_choices.md.

For OFFER_CLI: bandit uses uplift model prediction as the reward signal.
For FRAUD_CHECK / NOTHING: rule-based propensities (stretch: bandit
arms learning online).

This skeleton implements a clean exploration-exploitation interface;
the full Linear-TS posterior update lands in Day-5 (online learning hook
from the retraining_flow).

Extensible: replace `BanditPolicy` with a deeper architecture by
implementing the same `choose(context) -> Decision` protocol.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import numpy as np

from .inference import UpliftPrediction


class Action(enum.IntEnum):
    NOTHING = 0
    OFFER_CLI = 1
    FRAUD_CHECK = 2


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action
    propensity: float  # P(action | context, policy)
    expected_profit: float
    rationale: str  # short human-readable; logged in audit


@dataclass(frozen=True, slots=True)
class BanditContext:
    customer_id: str
    segment_id: int
    uplift: UpliftPrediction
    exposure: float
    fraud_score: float  # 0..1, from a rule-based detector for now (Day 3 stub)


# Cost parameters mirror docs/01_problem_and_domain.md cost-benefit table.
AVG_FRAUD_LOSS_USD: float = 500.0
FRAUD_CHECK_FRICTION_USD: float = 40.0


def _expected_profit_fraud_check(fraud_score: float) -> float:
    p = max(0.0, min(1.0, fraud_score))
    return p * AVG_FRAUD_LOSS_USD - (1 - p) * FRAUD_CHECK_FRICTION_USD


def choose(ctx: BanditContext) -> Decision:
    """Argmax over E[profit | action]. Returns the chosen action plus the
    propensity needed for the OPE harness (Day 6)."""
    profit_offer = ctx.uplift.expected_profit_treated(ctx.exposure)
    profit_fraud = _expected_profit_fraud_check(ctx.fraud_score)
    profit_nothing = 0.0

    profits = np.array([profit_nothing, profit_offer, profit_fraud])
    action_idx = int(profits.argmax())
    action = Action(action_idx)
    chosen_profit = float(profits[action_idx])

    # Softmax propensity over the three profits — gives OPE a smooth
    # propensity (no zero-probability arms even when argmax is decisive),
    # which keeps IPS/SNIPS estimators well-behaved.
    z = profits - profits.max()
    soft = np.exp(z / 10.0)  # temperature 10 — calibrate Day 4
    soft = soft / soft.sum()
    propensity = float(soft[action_idx])

    rationale = (
        f'argmax(offer={profit_offer:.2f}, '
        f'fraud={profit_fraud:.2f}, '
        f'nothing={profit_nothing:.2f}) → {action.name}'
    )
    return Decision(
        action=action,
        propensity=propensity,
        expected_profit=chosen_profit,
        rationale=rationale,
    )
