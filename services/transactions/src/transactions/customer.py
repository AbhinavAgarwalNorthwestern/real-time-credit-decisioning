"""Customer cohort generation with per-segment distributions.

Each Customer carries:
  - Observable attributes (features the decisioner can use)
  - Segment assignment (used for routing + evaluation, not as a model input)
  - Per-customer behavioral parameters (txn_rate, paydown_freq, mcc_weights)
  - Ground-truth causal response parameters (only OPE/validation sees these)

The ground-truth response parameters are:
  - true_p_accept_cli: probability of accepting a CLI offer
  - true_delta_spend_if_accept: incremental monthly spend if accepted
  - true_p_default: probability of default on new credit exposure

These are drawn from segment-specific Beta/Normal distributions
(see segments.py) and provide the analytical "right answer" for
validating the uplift model and OPE harness (Days 2 + 6).

See docs/dgp_design.md for the full DGP specification.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from transactions.segments import (
    SEGMENTS,
    SegmentParams,
)


@dataclass(frozen=True, slots=True)
class Customer:
    """One synthetic cardholder.

    Observable attributes (decisioner sees via feature MV):
        customer_id, segment_id, home_lat, home_lon, credit_limit,
        baseline_balance, credit_score, annual_income,
        account_tenure_months, n_products, prev_delinquency_count

    Behavioral parameters (drive event generation, observable in aggregate):
        txn_rate, paydown_freq, mcc_weights, card_present_rate

    Ground-truth response (only OPE / validation sees):
        true_p_accept_cli, true_delta_spend_if_accept, true_p_default
        (already adjusted by customer attributes — label_simulator must
        not double-count)
    """

    customer_id: str
    segment_id: int
    home_lat: float
    home_lon: float
    credit_limit: float
    baseline_balance: float

    # Customer-level attributes (constant per customer; feed risk model)
    credit_score: int
    annual_income: float
    account_tenure_months: int
    n_products: int
    prev_delinquency_count: int

    # Per-customer Poisson event rate (events/sec)
    txn_rate: float
    # Per-customer paydown frequency (probability per event)
    paydown_freq: float
    # Per-customer MCC weights (tuple of 12 floats matching MCC_CODES)
    mcc_weights: tuple[float, ...]
    # Card-present probability
    card_present_rate: float

    # Ground-truth response parameters
    true_p_accept_cli: float
    true_delta_spend_if_accept: float
    true_p_default: float


# US top-20 MSA centers (lat, lon) for population-weighted home locations
_MSA_CENTERS: list[tuple[float, float, float]] = [
    # (lat, lon, population_weight)
    (40.7128, -74.0060, 0.12),  # New York
    (34.0522, -118.2437, 0.10),  # Los Angeles
    (41.8781, -87.6298, 0.07),  # Chicago
    (29.7604, -95.3698, 0.06),  # Houston
    (33.4484, -112.0740, 0.05),  # Phoenix
    (29.4241, -98.4936, 0.04),  # San Antonio
    (32.7767, -96.7970, 0.05),  # Dallas
    (37.3382, -121.8863, 0.04),  # San Jose
    (30.2672, -97.7431, 0.04),  # Austin
    (39.7392, -104.9903, 0.04),  # Denver
    (47.6062, -122.3321, 0.03),  # Seattle
    (25.7617, -80.1918, 0.04),  # Miami
    (33.7490, -84.3880, 0.04),  # Atlanta
    (38.9072, -77.0369, 0.04),  # Washington DC
    (42.3601, -71.0589, 0.03),  # Boston
    (39.9526, -75.1652, 0.03),  # Philadelphia
    (32.7157, -117.1611, 0.03),  # San Diego
    (35.2271, -80.8431, 0.03),  # Charlotte
    (36.1627, -86.7816, 0.02),  # Nashville
    (45.5152, -122.6784, 0.02),  # Portland
]


def _sample_home_location(
    rng: np.random.Generator,
) -> tuple[float, float]:
    """Draw a home location from population-weighted MSA distribution."""
    weights = np.array([m[2] for m in _MSA_CENTERS])
    weights = weights / weights.sum()
    idx = int(rng.choice(len(_MSA_CENTERS), p=weights))
    center_lat, center_lon, _ = _MSA_CENTERS[idx]
    # Spread within the MSA (~30 km radius)
    lat = center_lat + float(rng.normal(0.0, 0.15))
    lon = center_lon + float(rng.normal(0.0, 0.15))
    return round(lat, 6), round(lon, 6)


def _draw_credit_limit(
    rng: np.random.Generator,
    seg: SegmentParams,
) -> float:
    """Draw credit limit from segment-specific categorical."""
    values = list(seg.credit_limits.keys())
    probs = list(seg.credit_limits.values())
    return float(rng.choice(values, p=probs))


def _draw_mcc_weights(
    rng: np.random.Generator,
    seg: SegmentParams,
) -> tuple[float, ...]:
    """Draw per-customer MCC weights from a Dirichlet centered on segment mean."""
    base = np.array(seg.mcc_weights, dtype=np.float64)
    alpha = base * seg.mcc_dirichlet_concentration
    weights = rng.dirichlet(alpha)
    return tuple(float(w) for w in weights)


def generate_cohort(size: int, seed: int) -> list[Customer]:
    """Deterministically generate a cohort of `size` customers.

    Customers are assigned to segments proportionally to population_share.
    Within each segment, all parameters are drawn from segment-specific
    distributions (see segments.py + docs/dgp_design.md).

    Same `seed` always yields the same cohort — makes Day 1 smoke tests
    and Day 5 drift demos reproducible.
    """
    rng = np.random.default_rng(seed)
    customers: list[Customer] = []

    # Assign segments proportionally
    shares = np.array([s.population_share for s in SEGMENTS])
    shares = shares / shares.sum()
    segment_counts = np.round(shares * size).astype(int)
    # Fix rounding to exactly match size
    diff = size - segment_counts.sum()
    segment_counts[0] += diff

    customer_idx = 0
    for seg_idx, seg in enumerate(SEGMENTS):
        count = int(segment_counts[seg_idx])
        for _ in range(count):
            home_lat, home_lon = _sample_home_location(rng)
            credit_limit = _draw_credit_limit(rng, seg)

            # Baseline utilization → starting balance
            util = float(rng.uniform(seg.util_low, seg.util_high))
            baseline_balance = round(credit_limit * util, 2)

            # Per-customer Poisson rate
            txn_rate = float(rng.gamma(seg.rate_gamma_shape, seg.rate_gamma_scale))

            # Paydown frequency
            paydown_freq = float(rng.beta(seg.paydown_beta_a, seg.paydown_beta_b))

            # Per-customer MCC weights
            mcc_weights = _draw_mcc_weights(rng, seg)

            # Ground-truth causal response parameters (base draws)
            true_p_accept_cli = float(
                rng.beta(seg.p_accept_beta_a, seg.p_accept_beta_b)
            )
            true_delta_spend = max(
                0.0, float(rng.normal(seg.delta_spend_mu, seg.delta_spend_sigma))
            )
            true_p_default = float(rng.beta(seg.p_default_beta_a, seg.p_default_beta_b))

            # Customer-level attributes (drawn from segment-specific distributions)
            credit_score = int(
                np.clip(
                    rng.normal(seg.credit_score_mu, seg.credit_score_sigma), 300, 850
                )
            )
            annual_income = round(
                float(
                    np.exp(rng.normal(seg.annual_income_mu, seg.annual_income_sigma))
                ),
                2,
            )
            tenure_months = int(
                np.clip(
                    rng.normal(seg.tenure_months_mu, seg.tenure_months_sigma), 1, 360
                )
            )
            n_products = int(np.clip(rng.poisson(seg.n_products_lambda), 1, 10))
            prev_delinquency = int(
                np.clip(rng.poisson(seg.prev_delinquency_lambda), 0, 20)
            )

            # Causal adjustments to base true_p_* from customer attributes.
            # label_simulator stays unchanged so context-dependent adjustments
            # (utilization, velocity, paydown) are NOT double-counted here.
            score_z = (credit_score - 700) / 150
            income_z = (np.log(annual_income) - 10.8) / 0.7
            tenure_z = (tenure_months - 48) / 48

            true_p_accept_cli = float(
                np.clip(
                    true_p_accept_cli
                    + 0.05 * score_z
                    + 0.03 * income_z
                    + 0.02 * (n_products / 5),
                    0.01,
                    0.95,
                )
            )
            true_delta_spend = max(0.0, true_delta_spend + income_z * 50)
            true_p_default = float(
                np.clip(
                    true_p_default
                    - 0.02 * score_z
                    + 0.01 * (prev_delinquency / 3)
                    - 0.01 * tenure_z,
                    0.001,
                    0.60,
                )
            )

            customers.append(
                Customer(
                    customer_id=f'cust-{customer_idx:06d}',
                    segment_id=seg.segment_id,
                    home_lat=home_lat,
                    home_lon=home_lon,
                    credit_limit=credit_limit,
                    baseline_balance=baseline_balance,
                    credit_score=credit_score,
                    annual_income=annual_income,
                    account_tenure_months=tenure_months,
                    n_products=n_products,
                    prev_delinquency_count=prev_delinquency,
                    txn_rate=round(txn_rate, 6),
                    paydown_freq=round(paydown_freq, 4),
                    mcc_weights=mcc_weights,
                    card_present_rate=seg.card_present_rate,
                    true_p_accept_cli=round(true_p_accept_cli, 6),
                    true_delta_spend_if_accept=round(true_delta_spend, 2),
                    true_p_default=round(true_p_default, 6),
                )
            )
            customer_idx += 1

    # Shuffle so segment order doesn't leak into customer_id ordering
    rng.shuffle(customers)
    return customers
