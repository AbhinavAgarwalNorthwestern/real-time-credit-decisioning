"""Fairness metrics library — group-conditional model performance + parity tests.

Computes the standard fair-lending fairness metrics for the credit decisioner:

- **Demographic parity (DP)** — equal positive-prediction rate across groups.
  Strictest fairness criterion; often impossible to satisfy if base rates differ.
- **Equalized odds (EOdds)** — equal TPR and equal FPR across groups. Allows
  base-rate differences to be reflected in predictions but demands equal
  error trade-offs. The fair-lending-defensible default.
- **Predictive parity (PP)** — equal positive predictive value (PPV) across
  groups. Often where a model that's well-calibrated globally is biased per-group.
- **Calibration parity** — equal calibration error per group. Tied to S1 work.

The credit-decisioning use case: groups are defined by `segment_id` (the
DGP's risk × tenure cross-product) AND by `credit_score_bucket` (decile
binning of credit_score). Per fair-lending regulation, these are PROXIES
for protected classes we cannot directly observe in synthetic data —
real deployment would also use ECOA-protected categories (race, sex, age).

Threshold conventions (industry standard):
- **80% rule** (EEOC "4/5ths rule"): the smallest group's metric value
  must be ≥ 80% of the largest group's. Used for demographic parity by
  most US regulators.
- **TPR/FPR parity within 10 percentage points**: common bank-internal
  threshold for equalized odds.

References:
- Hardt et al. (2016), "Equality of Opportunity in Supervised Learning"
- Barocas, Hardt, Narayanan (2019), "Fairness and Machine Learning"
- FFIEC "Fair Lending Examination Procedures" (2009)
- Mehrabi et al. (2021), "A Survey on Bias and Fairness in Machine Learning"

Cloud-agnostic: pure numpy + pandas. No Kafka, no Prometheus, no cluster.
The fairness library is testable in-process; the *service* wires it to
Kafka in `main.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# Thresholds; tunable via config in production
EIGHTY_PCT_RULE_FLOOR: float = 0.80
EQUALIZED_ODDS_TOLERANCE: float = 0.10  # 10 pp gap in TPR or FPR
PREDICTIVE_PARITY_TOLERANCE: float = 0.10


@dataclass(frozen=True, slots=True)
class GroupMetrics:
    """Per-group performance + prediction-rate statistics."""

    group_id: str
    n: int
    n_positive_actual: int
    n_positive_predicted: int
    positive_pred_rate: float  # Demographic parity component
    true_positive_rate: float | None  # TPR; None if no actual positives
    false_positive_rate: float | None  # FPR; None if no actual negatives
    positive_predictive_value: float | None  # PPV; None if no predicted positives


@dataclass(frozen=True, slots=True)
class FairnessReport:
    """Aggregate fairness assessment across all groups."""

    group_dimension: str  # e.g., 'segment_id' or 'credit_score_bucket'
    per_group: list[GroupMetrics]
    # Demographic parity ratio: smallest pred-rate / largest pred-rate (80% rule).
    # 1.0 = perfect parity; < 0.80 = violation.
    demographic_parity_ratio: float
    demographic_parity_pass: bool
    # Equalized odds gaps: max(TPR_gap, FPR_gap) across groups in absolute terms.
    equalized_odds_tpr_gap: float | None
    equalized_odds_fpr_gap: float | None
    equalized_odds_pass: bool
    # Predictive parity: max gap in PPV across groups.
    predictive_parity_ppv_gap: float | None
    predictive_parity_pass: bool
    violations: list[str] = field(default_factory=list)


def _compute_group_metrics(
    group_id: str,
    y_true_group: np.ndarray,
    y_pred_group: np.ndarray,
    threshold: float = 0.5,
) -> GroupMetrics:
    """Compute confusion-matrix-derived metrics for one group.

    `y_pred_group` may be probabilities (binarized at `threshold`) or already binary.
    """
    n = len(y_true_group)
    if n == 0:
        return GroupMetrics(
            group_id=group_id,
            n=0,
            n_positive_actual=0,
            n_positive_predicted=0,
            positive_pred_rate=0.0,
            true_positive_rate=None,
            false_positive_rate=None,
            positive_predictive_value=None,
        )

    y_true = y_true_group.astype(int)
    y_pred_binary = (y_pred_group >= threshold).astype(int)

    n_pos_actual = int(y_true.sum())
    n_neg_actual = n - n_pos_actual
    n_pos_pred = int(y_pred_binary.sum())

    # Confusion matrix
    tp = int(((y_pred_binary == 1) & (y_true == 1)).sum())
    fp = int(((y_pred_binary == 1) & (y_true == 0)).sum())

    tpr = tp / n_pos_actual if n_pos_actual > 0 else None
    fpr = fp / n_neg_actual if n_neg_actual > 0 else None
    ppv = tp / n_pos_pred if n_pos_pred > 0 else None
    positive_pred_rate = n_pos_pred / n

    return GroupMetrics(
        group_id=group_id,
        n=n,
        n_positive_actual=n_pos_actual,
        n_positive_predicted=n_pos_pred,
        positive_pred_rate=positive_pred_rate,
        true_positive_rate=tpr,
        false_positive_rate=fpr,
        positive_predictive_value=ppv,
    )


def compute_fairness_report(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    sensitive_attr: np.ndarray,
    group_dimension: str = 'group',
    threshold: float = 0.5,
    eighty_pct_rule_floor: float = EIGHTY_PCT_RULE_FLOOR,
    equalized_odds_tol: float = EQUALIZED_ODDS_TOLERANCE,
    predictive_parity_tol: float = PREDICTIVE_PARITY_TOLERANCE,
) -> FairnessReport:
    """Compute the full fairness report against three standard criteria.

    Parameters
    ----------
    y_true
        Binary actual outcomes (0/1).
    y_pred
        Predicted probabilities OR binary predictions.
    sensitive_attr
        Per-row group identifier (segment_id, credit_score_bucket, etc).
    group_dimension
        Label for the sensitive attribute, used in the report.
    threshold
        Decision threshold for converting probabilities to binary predictions.
    eighty_pct_rule_floor
        Demographic parity ratio floor (default 0.80 per EEOC 4/5ths rule).
    equalized_odds_tol
        Max acceptable absolute TPR or FPR gap (default 10 percentage points).
    predictive_parity_tol
        Max acceptable absolute PPV gap (default 10 percentage points).
    """
    if len(y_true) != len(y_pred) or len(y_true) != len(sensitive_attr):
        raise ValueError(
            f'length mismatch: y_true={len(y_true)} y_pred={len(y_pred)} '
            f'sensitive={len(sensitive_attr)}'
        )

    df = pd.DataFrame({'y': y_true, 'p': y_pred, 'g': sensitive_attr})
    per_group: list[GroupMetrics] = []
    for group_id, sub in df.groupby('g'):
        per_group.append(
            _compute_group_metrics(
                group_id=str(group_id),
                y_true_group=sub['y'].to_numpy(),
                y_pred_group=sub['p'].to_numpy(),
                threshold=threshold,
            )
        )
    per_group.sort(key=lambda m: m.group_id)

    # Demographic parity (80% rule)
    rates = [m.positive_pred_rate for m in per_group if m.n > 0]
    dp_ratio = (min(rates) / max(rates)) if rates and max(rates) > 0 else 1.0
    dp_pass = dp_ratio >= eighty_pct_rule_floor

    # Equalized odds: max gap in TPR and FPR across groups
    tprs = [m.true_positive_rate for m in per_group if m.true_positive_rate is not None]
    fprs = [
        m.false_positive_rate for m in per_group if m.false_positive_rate is not None
    ]
    tpr_gap = (max(tprs) - min(tprs)) if len(tprs) >= 2 else None
    fpr_gap = (max(fprs) - min(fprs)) if len(fprs) >= 2 else None
    eodds_pass = (tpr_gap is None or tpr_gap <= equalized_odds_tol) and (
        fpr_gap is None or fpr_gap <= equalized_odds_tol
    )

    # Predictive parity: max gap in PPV across groups
    ppvs = [
        m.positive_predictive_value
        for m in per_group
        if m.positive_predictive_value is not None
    ]
    ppv_gap = (max(ppvs) - min(ppvs)) if len(ppvs) >= 2 else None
    pp_pass = ppv_gap is None or ppv_gap <= predictive_parity_tol

    # Collect violations for downstream alerting
    violations: list[str] = []
    if not dp_pass:
        violations.append(
            f'demographic_parity: ratio={dp_ratio:.3f} < floor={eighty_pct_rule_floor:.2f}'
        )
    if tpr_gap is not None and tpr_gap > equalized_odds_tol:
        violations.append(
            f'equalized_odds_tpr: gap={tpr_gap:.3f} > tol={equalized_odds_tol:.2f}'
        )
    if fpr_gap is not None and fpr_gap > equalized_odds_tol:
        violations.append(
            f'equalized_odds_fpr: gap={fpr_gap:.3f} > tol={equalized_odds_tol:.2f}'
        )
    if ppv_gap is not None and ppv_gap > predictive_parity_tol:
        violations.append(
            f'predictive_parity: gap={ppv_gap:.3f} > tol={predictive_parity_tol:.2f}'
        )

    return FairnessReport(
        group_dimension=group_dimension,
        per_group=per_group,
        demographic_parity_ratio=dp_ratio,
        demographic_parity_pass=dp_pass,
        equalized_odds_tpr_gap=tpr_gap,
        equalized_odds_fpr_gap=fpr_gap,
        equalized_odds_pass=eodds_pass,
        predictive_parity_ppv_gap=ppv_gap,
        predictive_parity_pass=pp_pass,
        violations=violations,
    )


def credit_score_buckets(scores: np.ndarray, n_buckets: int = 5) -> np.ndarray:
    """Bucket credit scores for fairness analysis.

    Five-bucket default matches the credit-industry standard for fair-lending
    monitoring (Subprime / Near-prime / Prime / Super-prime / Elite). The
    actual cutoffs vary by issuer; for synthetic data we use quantiles.
    """
    s = pd.Series(scores)
    try:
        labels = pd.qcut(s, q=n_buckets, duplicates='drop', labels=False)
    except ValueError:
        labels = pd.Series(np.zeros(len(s), dtype=int))
    return labels.fillna(-1).astype(int).to_numpy()


def report_summary_lines(report: FairnessReport) -> list[str]:
    """Human-readable summary for logs and alerts."""
    lines = [
        f'Fairness report — dimension: {report.group_dimension}',
        f'  Demographic parity ratio: {report.demographic_parity_ratio:.3f} '
        f'(pass={report.demographic_parity_pass})',
    ]
    if report.equalized_odds_tpr_gap is not None:
        lines.append(
            f'  Equalized odds TPR gap: {report.equalized_odds_tpr_gap:.3f}, '
            f'FPR gap: {report.equalized_odds_fpr_gap or 0:.3f} '
            f'(pass={report.equalized_odds_pass})'
        )
    if report.predictive_parity_ppv_gap is not None:
        lines.append(
            f'  Predictive parity PPV gap: {report.predictive_parity_ppv_gap:.3f} '
            f'(pass={report.predictive_parity_pass})'
        )
    if report.violations:
        lines.append(f'  Violations: {len(report.violations)}')
        for v in report.violations:
            lines.append(f'    - {v}')
    else:
        lines.append('  No violations.')
    return lines
