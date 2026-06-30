"""Segment definitions for the synthetic credit-card DGP.

Six segments: {low, medium, high} risk × {tenured, new} tenure.
Each segment carries all distribution parameters used by customer.py,
generator.py, and distributions.py. Parameters are calibrated against
published consumer-credit aggregates — see docs/dgp_design.md for
rationale and citations.

All parameters are frozen dataclass fields. To adjust a segment's
behavior, change the values here; all downstream code reads from these
definitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class SegmentId(IntEnum):
    LOW_RISK_TENURED = 0
    LOW_RISK_NEW = 1
    MED_RISK_TENURED = 2
    MED_RISK_NEW = 3
    HIGH_RISK_TENURED = 4
    HIGH_RISK_NEW = 5


@dataclass(frozen=True, slots=True)
class SegmentParams:
    """All distribution parameters for one customer segment."""

    segment_id: SegmentId
    name: str
    population_share: float

    # --- Transaction rate: Gamma(shape, scale) → events/sec per customer ---
    rate_gamma_shape: float
    rate_gamma_scale: float

    # --- Amount: LogNormal(mu, sigma) in dollars ---
    amount_mu: float
    amount_sigma: float

    # --- Credit limit: {value: probability} ---
    credit_limits: dict[float, float] = field(default_factory=dict)

    # --- Baseline utilization: Uniform(low, high) ---
    util_low: float = 0.15
    util_high: float = 0.30

    # --- Ground-truth response: Beta(a, b) for probabilities ---
    p_accept_beta_a: float = 5.0
    p_accept_beta_b: float = 10.0
    delta_spend_mu: float = 500.0
    delta_spend_sigma: float = 80.0
    p_default_beta_a: float = 1.0
    p_default_beta_b: float = 49.0

    # --- Paydown frequency: Beta(a, b) ---
    paydown_beta_a: float = 8.0
    paydown_beta_b: float = 2.0

    # --- MCC weights (ordered same as MCC_CODES in distributions.py) ---
    mcc_weights: tuple[float, ...] = ()

    # --- Dirichlet concentration for per-customer MCC variation ---
    mcc_dirichlet_concentration: float = 50.0

    # --- Time-of-day peaks (two peak hours for double-sinusoid) ---
    circadian_peak_1: float = 12.0
    circadian_peak_2: float = 19.0

    # --- Session burst parameters ---
    p_session: float = 0.15
    session_factor: float = 3.0
    session_duration_s: float = 600.0  # 10 minutes

    # --- Geographic dispersion ---
    geo_sigma_local: float = 0.03
    p_travel: float = 0.05
    geo_sigma_travel: float = 2.0

    # --- Customer-level attributes: per-segment distribution params ---
    credit_score_mu: float = 700.0
    credit_score_sigma: float = 40.0
    annual_income_mu: float = 10.8  # log-scale (exp(10.8) ≈ $49k)
    annual_income_sigma: float = 0.5
    tenure_months_mu: float = 60.0
    tenure_months_sigma: float = 20.0
    n_products_lambda: float = 2.5
    prev_delinquency_lambda: float = 0.3

    # --- Card-present rate ---
    card_present_rate: float = 0.70


# MCC codes in canonical order (matches distributions.py)
MCC_CODES: tuple[int, ...] = (
    5411,  # Grocery
    5812,  # Restaurants
    5541,  # Gas stations
    5311,  # Department stores
    5912,  # Pharmacy
    5651,  # Clothing
    4900,  # Utilities
    4814,  # Telecom
    7011,  # Lodging
    6010,  # Cash advance
    5999,  # Misc retail
    0,  # Other
)


SEGMENTS: tuple[SegmentParams, ...] = (
    SegmentParams(
        segment_id=SegmentId.LOW_RISK_TENURED,
        name='low_risk_tenured',
        population_share=0.25,
        rate_gamma_shape=3.0,
        rate_gamma_scale=0.015,
        amount_mu=4.2,
        amount_sigma=0.8,
        credit_limits={5000.0: 0.3, 10000.0: 0.4, 15000.0: 0.2, 25000.0: 0.1},
        util_low=0.15,
        util_high=0.30,
        p_accept_beta_a=5.0,
        p_accept_beta_b=10.0,
        delta_spend_mu=500.0,
        delta_spend_sigma=80.0,
        p_default_beta_a=1.0,
        p_default_beta_b=49.0,
        paydown_beta_a=8.0,
        paydown_beta_b=2.0,
        mcc_weights=(
            0.22,
            0.16,
            0.12,
            0.12,
            0.08,
            0.08,
            0.06,
            0.04,
            0.06,
            0.01,
            0.03,
            0.02,
        ),
        mcc_dirichlet_concentration=50.0,
        circadian_peak_1=12.0,
        circadian_peak_2=19.0,
        p_session=0.15,
        session_factor=3.0,
        session_duration_s=600.0,
        geo_sigma_local=0.03,
        p_travel=0.05,
        geo_sigma_travel=2.0,
        credit_score_mu=760.0,
        credit_score_sigma=30.0,
        annual_income_mu=11.2,
        annual_income_sigma=0.4,
        tenure_months_mu=96.0,
        tenure_months_sigma=24.0,
        n_products_lambda=3.5,
        prev_delinquency_lambda=0.1,
        card_present_rate=0.75,
    ),
    SegmentParams(
        segment_id=SegmentId.LOW_RISK_NEW,
        name='low_risk_new',
        population_share=0.15,
        rate_gamma_shape=2.0,
        rate_gamma_scale=0.012,
        amount_mu=3.8,
        amount_sigma=0.7,
        credit_limits={2000.0: 0.3, 5000.0: 0.5, 10000.0: 0.2},
        util_low=0.10,
        util_high=0.25,
        p_accept_beta_a=3.0,
        p_accept_beta_b=9.0,
        delta_spend_mu=400.0,
        delta_spend_sigma=100.0,
        p_default_beta_a=1.0,
        p_default_beta_b=32.0,
        paydown_beta_a=6.0,
        paydown_beta_b=3.0,
        mcc_weights=(
            0.18,
            0.14,
            0.10,
            0.14,
            0.06,
            0.10,
            0.05,
            0.04,
            0.08,
            0.02,
            0.05,
            0.04,
        ),
        mcc_dirichlet_concentration=50.0,
        circadian_peak_1=14.0,
        circadian_peak_2=20.0,
        p_session=0.20,
        session_factor=4.0,
        session_duration_s=900.0,
        geo_sigma_local=0.05,
        p_travel=0.03,
        geo_sigma_travel=1.5,
        credit_score_mu=720.0,
        credit_score_sigma=35.0,
        annual_income_mu=11.0,
        annual_income_sigma=0.5,
        tenure_months_mu=12.0,
        tenure_months_sigma=6.0,
        n_products_lambda=1.5,
        prev_delinquency_lambda=0.2,
        card_present_rate=0.72,
    ),
    SegmentParams(
        segment_id=SegmentId.MED_RISK_TENURED,
        name='med_risk_tenured',
        population_share=0.20,
        rate_gamma_shape=3.0,
        rate_gamma_scale=0.018,
        amount_mu=3.7,
        amount_sigma=0.9,
        credit_limits={2000.0: 0.2, 5000.0: 0.5, 10000.0: 0.3},
        util_low=0.35,
        util_high=0.55,
        p_accept_beta_a=4.0,
        p_accept_beta_b=9.0,
        delta_spend_mu=400.0,
        delta_spend_sigma=120.0,
        p_default_beta_a=2.0,
        p_default_beta_b=31.0,
        paydown_beta_a=4.0,
        paydown_beta_b=4.0,
        mcc_weights=(
            0.18,
            0.12,
            0.12,
            0.10,
            0.08,
            0.06,
            0.08,
            0.05,
            0.04,
            0.05,
            0.06,
            0.06,
        ),
        mcc_dirichlet_concentration=30.0,
        circadian_peak_1=12.0,
        circadian_peak_2=18.0,
        p_session=0.15,
        session_factor=3.0,
        session_duration_s=600.0,
        geo_sigma_local=0.04,
        p_travel=0.04,
        geo_sigma_travel=2.0,
        credit_score_mu=680.0,
        credit_score_sigma=40.0,
        annual_income_mu=10.8,
        annual_income_sigma=0.5,
        tenure_months_mu=72.0,
        tenure_months_sigma=24.0,
        n_products_lambda=3.0,
        prev_delinquency_lambda=0.5,
        card_present_rate=0.70,
    ),
    SegmentParams(
        segment_id=SegmentId.MED_RISK_NEW,
        name='med_risk_new',
        population_share=0.15,
        rate_gamma_shape=2.5,
        rate_gamma_scale=0.014,
        amount_mu=3.5,
        amount_sigma=0.8,
        credit_limits={1000.0: 0.3, 2000.0: 0.4, 5000.0: 0.3},
        util_low=0.30,
        util_high=0.50,
        p_accept_beta_a=3.0,
        p_accept_beta_b=12.0,
        delta_spend_mu=300.0,
        delta_spend_sigma=100.0,
        p_default_beta_a=2.0,
        p_default_beta_b=23.0,
        paydown_beta_a=3.0,
        paydown_beta_b=5.0,
        mcc_weights=(
            0.15,
            0.10,
            0.10,
            0.08,
            0.06,
            0.08,
            0.08,
            0.06,
            0.03,
            0.08,
            0.08,
            0.10,
        ),
        mcc_dirichlet_concentration=30.0,
        circadian_peak_1=15.0,
        circadian_peak_2=21.0,
        p_session=0.25,
        session_factor=4.0,
        session_duration_s=1200.0,
        geo_sigma_local=0.06,
        p_travel=0.03,
        geo_sigma_travel=1.5,
        credit_score_mu=650.0,
        credit_score_sigma=45.0,
        annual_income_mu=10.6,
        annual_income_sigma=0.6,
        tenure_months_mu=8.0,
        tenure_months_sigma=4.0,
        n_products_lambda=1.2,
        prev_delinquency_lambda=0.8,
        card_present_rate=0.68,
    ),
    SegmentParams(
        segment_id=SegmentId.HIGH_RISK_TENURED,
        name='high_risk_tenured',
        population_share=0.15,
        rate_gamma_shape=4.0,
        rate_gamma_scale=0.020,
        amount_mu=3.3,
        amount_sigma=1.1,
        credit_limits={500.0: 0.2, 1000.0: 0.4, 2000.0: 0.3, 5000.0: 0.1},
        util_low=0.60,
        util_high=0.80,
        p_accept_beta_a=6.0,
        p_accept_beta_b=9.0,
        delta_spend_mu=250.0,
        delta_spend_sigma=80.0,
        p_default_beta_a=3.0,
        p_default_beta_b=22.0,
        paydown_beta_a=2.0,
        paydown_beta_b=6.0,
        mcc_weights=(
            0.14,
            0.08,
            0.10,
            0.06,
            0.06,
            0.04,
            0.10,
            0.06,
            0.02,
            0.14,
            0.10,
            0.10,
        ),
        mcc_dirichlet_concentration=15.0,
        circadian_peak_1=13.0,
        circadian_peak_2=22.0,
        p_session=0.30,
        session_factor=5.0,
        session_duration_s=1800.0,
        geo_sigma_local=0.08,
        p_travel=0.02,
        geo_sigma_travel=1.0,
        credit_score_mu=600.0,
        credit_score_sigma=50.0,
        annual_income_mu=10.4,
        annual_income_sigma=0.6,
        tenure_months_mu=60.0,
        tenure_months_sigma=30.0,
        n_products_lambda=2.5,
        prev_delinquency_lambda=1.5,
        card_present_rate=0.62,
    ),
    SegmentParams(
        segment_id=SegmentId.HIGH_RISK_NEW,
        name='high_risk_new',
        population_share=0.10,
        rate_gamma_shape=3.5,
        rate_gamma_scale=0.020,
        amount_mu=3.1,
        amount_sigma=1.0,
        credit_limits={500.0: 0.4, 1000.0: 0.4, 2000.0: 0.2},
        util_low=0.65,
        util_high=0.85,
        p_accept_beta_a=7.0,
        p_accept_beta_b=8.0,
        delta_spend_mu=200.0,
        delta_spend_sigma=60.0,
        p_default_beta_a=3.0,
        p_default_beta_b=17.0,
        paydown_beta_a=1.0,
        paydown_beta_b=7.0,
        mcc_weights=(
            0.12,
            0.06,
            0.08,
            0.05,
            0.05,
            0.04,
            0.10,
            0.06,
            0.02,
            0.18,
            0.12,
            0.12,
        ),
        mcc_dirichlet_concentration=15.0,
        circadian_peak_1=14.0,
        circadian_peak_2=23.0,
        p_session=0.35,
        session_factor=5.0,
        session_duration_s=1800.0,
        geo_sigma_local=0.10,
        p_travel=0.01,
        geo_sigma_travel=0.5,
        credit_score_mu=570.0,
        credit_score_sigma=55.0,
        annual_income_mu=10.2,
        annual_income_sigma=0.7,
        tenure_months_mu=6.0,
        tenure_months_sigma=3.0,
        n_products_lambda=1.0,
        prev_delinquency_lambda=2.5,
        card_present_rate=0.58,
    ),
)

SEGMENT_BY_ID: dict[SegmentId, SegmentParams] = {s.segment_id: s for s in SEGMENTS}

# Day-of-week multipliers (Mon=0 .. Sun=6)
DOW_MULTIPLIERS: tuple[float, ...] = (0.85, 0.90, 0.95, 1.00, 1.15, 1.20, 1.05)
