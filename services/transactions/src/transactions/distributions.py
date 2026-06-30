"""Probability distributions for the transaction generator.

Provides:
  - Per-customer MCC sampling (using customer-specific Dirichlet weights)
  - Per-segment amount sampling (lognormal with segment parameters)
  - Circadian (time-of-day) rate modulation
  - Day-of-week modulation
  - Geographic perturbation with travel events
  - Session burst scheduling

All distribution parameters come from segments.py; this module provides
the sampling mechanics.

See docs/dgp_design.md for calibration rationale.
"""

from __future__ import annotations

import math

import numpy as np

from transactions.segments import (
    DOW_MULTIPLIERS,
    MCC_CODES,
    SegmentParams,
)


def sample_mcc(rng: np.random.Generator, mcc_weights: tuple[float, ...]) -> int:
    """Draw one MCC code using per-customer weights."""
    idx = int(rng.choice(len(MCC_CODES), p=mcc_weights))
    return MCC_CODES[idx]


def sample_amount(rng: np.random.Generator, seg: SegmentParams, mcc: int) -> float:
    """Draw transaction amount from segment-specific lognormal.

    Cash advances (MCC 6010) are typically larger with higher variance.
    """
    mu = seg.amount_mu
    sigma = seg.amount_sigma

    if mcc == 6010:
        mu += 0.5
        sigma += 0.3

    amount = float(rng.lognormal(mean=mu, sigma=sigma))
    return round(amount, 2)


def circadian_multiplier(hour: float, seg: SegmentParams) -> float:
    """Compute time-of-day activity multiplier for a segment.

    Double-peaked sinusoid with segment-specific peak hours.
    Returns value in [0.2, 1.0].
    """
    p1 = seg.circadian_peak_1
    p2 = seg.circadian_peak_2

    m1 = math.cos((hour - p1) * math.pi / 12.0) * 0.4 + 0.6
    m2 = math.cos((hour - p2) * math.pi / 12.0) * 0.3 + 0.7

    return max(min(max(m1, m2), 1.0), 0.2)


def dow_multiplier(day_of_week: int) -> float:
    """Day-of-week activity multiplier (Mon=0 .. Sun=6)."""
    return DOW_MULTIPLIERS[day_of_week]


def perturb_geo(
    home_lat: float,
    home_lon: float,
    rng: np.random.Generator,
    seg: SegmentParams,
) -> tuple[float, float]:
    """Perturb home location to produce transaction geo.

    With probability p_travel, produces a far-from-home event (travel).
    Otherwise, local perturbation with segment-specific sigma.
    """
    if float(rng.random()) < seg.p_travel:
        sigma = seg.geo_sigma_travel
    else:
        sigma = seg.geo_sigma_local

    lat = home_lat + float(rng.normal(0.0, sigma))
    lon = home_lon + float(rng.normal(0.0, sigma))
    return round(lat, 6), round(lon, 6)
