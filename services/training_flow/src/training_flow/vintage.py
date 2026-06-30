"""Vintage analysis — Phase B Item S2.

Vintage analysis is the credit-industry standard for tracking how default
rates evolve over the lifetime of a cohort grouped by **origination date**
(the month/quarter a customer's account was opened). It's the chart every
credit risk reviewer asks for first, because it reveals:

- **Macroeconomic regime shifts**: 2008 cohorts default at 3x the rate of
  2005 cohorts at the same months-on-book.
- **Underwriting drift**: a sudden uptick in early-life defaults for newer
  cohorts → underwriting standards have loosened.
- **Seasoning curves**: default rates aren't constant — they peak around
  months 6-18 then decline. Capital-allocation models use these curves to
  forecast portfolio losses.

This module computes:
- `vintage_curves` — for each origination cohort and each months-on-book bucket,
  the cumulative default rate
- `vintage_summary` — peak default month per cohort, total observed default rate

Used in `ml_evaluation.ipynb` §14 after Step 12.6 retrains the model.

References:
- Anderson (2007), "The Credit Scoring Toolkit" §10
- Standard FRB SR 11-7 model validation expectations

Cloud-agnostic: pure pandas + numpy. No cluster, no cloud, no network.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True, slots=True)
class VintagePoint:
    """One (origination_cohort, months_on_book) → default_rate point."""

    origination_cohort: str
    months_on_book: int
    n_accounts: int
    n_defaults_cumulative: int
    cumulative_default_rate: float


@dataclass(frozen=True, slots=True)
class VintageSummary:
    """Aggregate per-cohort statistics."""

    origination_cohort: str
    n_accounts: int
    peak_default_month: int
    peak_cumulative_default_rate: float
    final_cumulative_default_rate: float
    observation_horizon_months: int


def compute_vintage_curves(
    df: pd.DataFrame,
    origination_col: str,
    default_col: str,
    default_month_col: str,
    max_months_on_book: int = 24,
    cohort_freq: str = 'M',
) -> list[VintagePoint]:
    """Compute cumulative default rates by (origination cohort, months-on-book).

    Parameters
    ----------
    df
        Per-account DataFrame.
    origination_col
        Column with each account's origination date (pandas-datetime-compatible).
    default_col
        Binary column: 1 if the account ever defaulted in the observation
        window, 0 otherwise.
    default_month_col
        Integer column: months-on-book at which the default occurred.
        Ignored when default_col == 0.
    max_months_on_book
        Cap on the months-on-book horizon to report.
    cohort_freq
        pandas frequency alias for grouping origination dates. Default 'M'
        (monthly cohorts); 'Q' for quarterly; 'Y' for annual.

    Returns
    -------
    List of VintagePoint records, one per (cohort, months_on_book).
    """
    required = {origination_col, default_col, default_month_col}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f'missing columns: {missing}')

    local = df[[origination_col, default_col, default_month_col]].copy()
    local[origination_col] = pd.to_datetime(local[origination_col])
    local['cohort'] = local[origination_col].dt.to_period(cohort_freq).astype(str)

    points: list[VintagePoint] = []
    for cohort, sub in local.groupby('cohort'):
        n_total = len(sub)
        for mob in range(1, max_months_on_book + 1):
            cum_defaults = int(
                ((sub[default_col] == 1) & (sub[default_month_col] <= mob)).sum()
            )
            points.append(
                VintagePoint(
                    origination_cohort=str(cohort),
                    months_on_book=mob,
                    n_accounts=n_total,
                    n_defaults_cumulative=cum_defaults,
                    cumulative_default_rate=cum_defaults / max(n_total, 1),
                )
            )
    return points


def vintage_curves_to_dataframe(points: list[VintagePoint]) -> pd.DataFrame:
    """Convert a list of VintagePoint to a tidy DataFrame for plotting."""
    return pd.DataFrame(
        [
            {
                'origination_cohort': p.origination_cohort,
                'months_on_book': p.months_on_book,
                'n_accounts': p.n_accounts,
                'n_defaults_cumulative': p.n_defaults_cumulative,
                'cumulative_default_rate': p.cumulative_default_rate,
            }
            for p in points
        ]
    )


def compute_vintage_summary(points: list[VintagePoint]) -> list[VintageSummary]:
    """Aggregate per-cohort summary statistics."""
    df = vintage_curves_to_dataframe(points)
    if df.empty:
        return []

    summaries: list[VintageSummary] = []
    for cohort, sub in df.groupby('origination_cohort'):
        sub_sorted = sub.sort_values('months_on_book')
        # peak month = first month where cumulative_default_rate is at max
        max_rate = float(sub_sorted['cumulative_default_rate'].max())
        peak_row = sub_sorted[sub_sorted['cumulative_default_rate'] == max_rate].iloc[0]
        final_row = sub_sorted.iloc[-1]
        summaries.append(
            VintageSummary(
                origination_cohort=str(cohort),
                n_accounts=int(sub_sorted['n_accounts'].iloc[0]),
                peak_default_month=int(peak_row['months_on_book']),
                peak_cumulative_default_rate=max_rate,
                final_cumulative_default_rate=float(
                    final_row['cumulative_default_rate']
                ),
                observation_horizon_months=int(sub_sorted['months_on_book'].max()),
            )
        )
    return summaries


def detect_vintage_drift(
    points: list[VintagePoint],
    drift_threshold: float = 0.50,
    comparison_mob: int = 12,
) -> list[tuple[str, str, float]]:
    """Identify cohorts whose default rate is materially worse than predecessors.

    For each consecutive pair of cohorts (sorted by origination), compares
    the cumulative default rate at month `comparison_mob`. Flags pairs where
    the newer cohort's rate exceeds the older cohort's by `drift_threshold`
    (relative — e.g., 0.50 = 50% worse).

    Returns a list of (older_cohort, newer_cohort, relative_change) tuples
    for the flagged pairs. Empty list means no drift detected.
    """
    df = vintage_curves_to_dataframe(points)
    df = df[df['months_on_book'] == comparison_mob].sort_values('origination_cohort')
    if len(df) < 2:
        return []

    flags: list[tuple[str, str, float]] = []
    rows = list(df.itertuples(index=False))
    for older, newer in zip(rows[:-1], rows[1:], strict=True):
        if older.cumulative_default_rate <= 0:
            continue  # avoid division by zero on zero-default cohorts
        relative = (
            newer.cumulative_default_rate - older.cumulative_default_rate
        ) / older.cumulative_default_rate
        if relative >= drift_threshold:
            flags.append(
                (
                    str(older.origination_cohort),
                    str(newer.origination_cohort),
                    float(relative),
                )
            )
    return flags
