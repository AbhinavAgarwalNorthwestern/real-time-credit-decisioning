"""Reject inference — Phase D Item A8.

Standard credit scorecard problem: training data only contains the BEHAVIOR
of accepted applicants (we don't know how rejected applicants would have
performed). Naively training on accepts-only produces a biased model that
underestimates risk for the rejected population.

Two common solutions:

- **Fuzzy parceling (a.k.a. extrapolation)**: assign each rejected applicant
  TWO synthetic outcomes — one as a default with weight p_default, one as
  a non-default with weight 1-p_default. Doubles the rejected rows in the
  augmented training set. Standard in retail credit (Siddiqi §10).

- **Heckman selection model**: two-stage regression. Stage 1 predicts
  P(accepted | applicant features) → inverse Mills ratio. Stage 2 predicts
  P(default | features, IMR). Econometric pedigree (Heckman 1979); requires
  identifying restrictions for exclusion of variables from stage 2.

In our synthetic-DGP setup, we don't actually have rejected applicants
because the bandit accepts everyone for exploration. But we SIMULATE the
reject inference problem by:
1. Holding out a "rejected" subpopulation (e.g., those with low predicted
   p_accept_cli)
2. Treating their actual outcomes as unknown
3. Applying reject inference to recover an unbiased default model

Used as a research artifact in `ml_evaluation.ipynb` post-Phase-B to
demonstrate the technique.

References:
- Siddiqi (2017) §10.1-10.3
- Heckman (1979), "Sample Selection Bias as a Specification Error"
- Mays (2001), "Handbook of Credit Scoring" ch. 17

Cloud-agnostic: pure numpy/pandas/sklearn.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression


@dataclass(frozen=True, slots=True)
class FuzzyParcelResult:
    """Output of fuzzy parceling — augmented training set ready for fitting."""

    X_augmented: np.ndarray  # shape (n_accept + 2*n_reject, n_features)
    y_augmented: np.ndarray  # binary defaults
    weights: np.ndarray  # per-row training weight
    n_accepts: int
    n_rejects: int


@dataclass(frozen=True, slots=True)
class HeckmanResult:
    """Output of Heckman two-stage selection model."""

    stage1_classifier: LogisticRegression  # selection equation
    stage2_classifier: LogisticRegression  # default equation given IMR
    feature_names_stage1: tuple[str, ...]
    feature_names_stage2: tuple[str, ...]
    n_observed: int  # accepted applicants used for stage 2
    n_total: int  # all applicants for stage 1


def fuzzy_parcel(
    X_accept: np.ndarray,
    y_accept: np.ndarray,
    X_reject: np.ndarray,
    p_default_reject: np.ndarray,
) -> FuzzyParcelResult:
    """Augment training data via fuzzy parceling.

    For each rejected applicant:
    - Add one row labeled `1` (defaulted) with weight `p_default_reject`
    - Add one row labeled `0` (non-default) with weight `1 - p_default_reject`

    Accepts retain their original label with weight 1.

    Parameters
    ----------
    X_accept
        Feature matrix for accepted applicants (whose outcomes we observed).
    y_accept
        Observed binary default labels for accepts.
    X_reject
        Feature matrix for rejected applicants (no observed outcome).
    p_default_reject
        Per-rejected-applicant estimated probability of default. Typically
        obtained from a first-pass model trained on accepts only — the
        "naive" model — that we want to debias.
    """
    n_accept = len(X_accept)
    n_reject = len(X_reject)

    X_aug = np.vstack(
        [
            X_accept,
            X_reject,  # synthetic default
            X_reject,  # synthetic non-default
        ]
    )
    y_aug = np.concatenate(
        [
            y_accept,
            np.ones(n_reject, dtype=int),
            np.zeros(n_reject, dtype=int),
        ]
    )
    w_aug = np.concatenate(
        [
            np.ones(n_accept, dtype=float),
            np.asarray(p_default_reject, dtype=float),
            1.0 - np.asarray(p_default_reject, dtype=float),
        ]
    )
    return FuzzyParcelResult(
        X_augmented=X_aug,
        y_augmented=y_aug,
        weights=w_aug,
        n_accepts=n_accept,
        n_rejects=n_reject,
    )


def fit_with_fuzzy_parcel(
    parcel: FuzzyParcelResult,
    random_state: int = 42,
) -> LogisticRegression:
    """Fit a logistic regression on the augmented + weighted training set."""
    clf = LogisticRegression(max_iter=500, random_state=random_state, solver='lbfgs')
    clf.fit(parcel.X_augmented, parcel.y_augmented, sample_weight=parcel.weights)
    return clf


def _inverse_mills_ratio(scores: np.ndarray) -> np.ndarray:
    """Compute the inverse Mills ratio: φ(z) / Φ(z).

    Used in the Heckman correction. `scores` are stage-1 selection-equation
    predictions on the logit scale (or any monotonic transform thereof).
    Implementation uses the standard-normal PDF/CDF ratio.
    """
    from scipy.stats import norm

    return norm.pdf(scores) / np.clip(norm.cdf(scores), 1e-9, None)


def heckman_two_stage(
    X_selection: np.ndarray,
    accepted: np.ndarray,
    X_outcome: np.ndarray,
    y_outcome: np.ndarray,
    feature_names_stage1: list[str] | None = None,
    feature_names_stage2: list[str] | None = None,
    random_state: int = 42,
) -> HeckmanResult:
    """Heckman two-stage selection model for reject inference.

    Stage 1: model P(accepted | X_selection) for ALL applicants.
    Stage 2: model P(default | X_outcome + IMR) for ACCEPTED applicants only.

    Returns the fitted models; downstream code applies stage 2 (with the
    IMR augmented) to NEW applicants to get unbiased default predictions.

    `accepted` is binary {0, 1} indicating acceptance. `y_outcome` may be
    NaN/sentinel for rejects; we slice accepted rows only.
    """
    # Stage 1: who got accepted?
    stage1 = LogisticRegression(max_iter=500, random_state=random_state, solver='lbfgs')
    stage1.fit(X_selection, accepted.astype(int))
    selection_scores = stage1.decision_function(X_selection)
    imr = _inverse_mills_ratio(selection_scores)

    # Stage 2: among accepted, model default using outcome features + IMR
    accepted_mask = accepted.astype(bool)
    X2 = np.column_stack([X_outcome[accepted_mask], imr[accepted_mask]])
    y2 = y_outcome[accepted_mask].astype(int)
    stage2 = LogisticRegression(max_iter=500, random_state=random_state, solver='lbfgs')
    stage2.fit(X2, y2)

    return HeckmanResult(
        stage1_classifier=stage1,
        stage2_classifier=stage2,
        feature_names_stage1=tuple(feature_names_stage1 or []),
        feature_names_stage2=tuple(
            (feature_names_stage2 or []) + ['inverse_mills_ratio']
        ),
        n_observed=int(accepted_mask.sum()),
        n_total=len(accepted),
    )


def predict_with_heckman(
    heckman: HeckmanResult,
    X_selection: np.ndarray,
    X_outcome: np.ndarray,
) -> np.ndarray:
    """Predict P(default) using the Heckman-corrected stage-2 model.

    For each applicant: compute stage-1 selection score → IMR → augment
    stage-2 features → stage-2 default probability.
    """
    selection_scores = heckman.stage1_classifier.decision_function(X_selection)
    imr = _inverse_mills_ratio(selection_scores)
    X2 = np.column_stack([X_outcome, imr])
    return np.asarray(heckman.stage2_classifier.predict_proba(X2)[:, 1], dtype=float)


def simulate_rejected_population(
    df: pd.DataFrame,
    p_reject_threshold: float = 0.30,
    p_accept_col: str = 'true_p_accept_cli',
    random_state: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Synthetic-data helper: split a cohort into "accepts" and "rejects".

    Customers with low `p_accept_col` are deemed rejected. Useful for
    DEMONSTRATING reject inference on synthetic data — in real banks
    the rejection logic is per the lender's policy.

    Returns (accepts_df, rejects_df).
    """
    accepts = df[df[p_accept_col] >= p_reject_threshold].copy()
    rejects = df[df[p_accept_col] < p_reject_threshold].copy()
    return accepts, rejects
