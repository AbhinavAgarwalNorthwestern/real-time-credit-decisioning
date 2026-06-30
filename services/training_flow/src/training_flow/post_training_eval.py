"""Post-training evaluation orchestrator — wires all credit-stats libraries.

After the training pipeline produces a champion model, this module runs the
full battery of credit-domain evaluations and writes everything as MLflow
artifacts attached to the model version:

1. **Discrimination** — Gini, KS, AUC, Lorenz curve (`discrimination.py`)
2. **Calibration** — Hosmer-Lemeshow, Brier, ECE, calibration curve, isotonic
   recalibrator (`calibration.py`)
3. **Vintage** — cohort-by-month default curves (`vintage.py`)
4. **Loss decomposition** — PD × LGD × EAD per Basel IRB (`loss_forecasting.py`)
5. **Stress test** — DFAST baseline / adverse / severely adverse (`stress_test.py`)
6. **Model card** — auto-rendered Mitchell-et-al markdown + SR-11-7 checklist
   (`model_card.py`)

Fairness is INTENTIONALLY excluded here — the `bias_monitor` service runs
against the LIVE decisions+outcomes Kafka stream in production, not the
training-time data. Train-time fairness checks would only catch issues in
the training distribution; the live monitor catches drift in serving.

This module is intra-training_flow (no cross-service deps). Callers are
`training_flow/__main__.py` and the retraining flow's `flow.py`.

Cloud-agnostic: writes artifacts via MLflow client; same code runs on dev
MinIO or AWS S3 backend.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from training_flow.calibration import (
    CalibrationResult,
    calibration_summary,
)
from training_flow.discrimination import (
    DiscriminationResult,
    discrimination_summary,
)
from training_flow.loss_forecasting import (
    DEFAULT_DRAW_FACTOR,
    DEFAULT_LGD_RETAIL_CARD,
    loss_components_frame,
)
from training_flow.model_card import (
    ModelCardInputs,
    PerformanceMetrics,
    StressTestSummary,
    render_model_card_markdown,
    render_sr_11_7_checklist_markdown,
)
from training_flow.stress_test import (
    STANDARD_SCENARIOS,
    ScenarioResult,
    run_stress_test,
)
from training_flow.vintage import (
    VintagePoint,
    compute_vintage_curves,
)


@dataclass(frozen=True, slots=True)
class PostTrainingEvalResult:
    """Aggregate output of the full evaluation suite.

    Each field is None when the input data didn't support that evaluation
    (e.g., no `default_month` column → no vintage). Downstream code
    (model card, MLflow artifact log) handles None gracefully.
    """

    discrimination: DiscriminationResult | None = None
    calibration: CalibrationResult | None = None
    vintage_points: list[VintagePoint] = field(default_factory=list)
    stress_test: list[ScenarioResult] = field(default_factory=list)
    loss_decomposition: pd.DataFrame | None = None
    model_card_markdown: str = ''
    sr_11_7_checklist_markdown: str = ''


def run_full_eval(
    holdout_df: pd.DataFrame,
    predicted_pd: np.ndarray,
    actual_default: np.ndarray,
    predict_pd_fn,
    current_balance_col: str,
    credit_limit_col: str,
    *,
    customer_id_col: str = 'customer_id',
    origination_col: str | None = None,
    default_month_col: str | None = None,
    model_name: str = 'credit_t_learner',
    model_version: str = 'unknown',
    mlflow_run_id: str = 'unknown',
    master_seed: int = 42,
    feature_cols: tuple[str, ...] = (),
    champion_alias: str = 'challenger',
    monotonicity_constraints: dict[str, str] | None = None,
    adverse_action_reason_codes: dict[str, str] | None = None,
    n_bins: int = 10,
    base_lgd: float = DEFAULT_LGD_RETAIL_CARD,
    draw_factor: float = DEFAULT_DRAW_FACTOR,
) -> PostTrainingEvalResult:
    """Run all credit-stats evaluations on a holdout + trained model.

    Parameters
    ----------
    holdout_df
        OOT holdout DataFrame.
    predicted_pd
        Per-row predicted PD from the trained model.
    actual_default
        Per-row binary default outcomes from OOT.
    predict_pd_fn
        `predict_pd_fn(df) -> np.ndarray` — used by stress_test to re-score
        the cohort under perturbed scenarios.
    current_balance_col / credit_limit_col
        Columns for EAD computation.
    origination_col / default_month_col
        Optional — needed for vintage analysis. Skip gracefully if absent.
    model_name / model_version / mlflow_run_id / master_seed / feature_cols
        Provenance metadata for the model card.
    champion_alias
        'champion' or 'challenger'.
    monotonicity_constraints / adverse_action_reason_codes
        Optional model-card metadata.
    n_bins / base_lgd / draw_factor
        Standard tuning knobs.
    """
    pd_arr = np.asarray(predicted_pd, dtype=float)
    y_arr = np.asarray(actual_default, dtype=int)

    # 1. Discrimination
    discr = (
        discrimination_summary(y_arr, pd_arr, n_bins=n_bins)
        if len(np.unique(y_arr)) > 1
        else None
    )

    # 2. Calibration
    calib = (
        calibration_summary(y_arr, pd_arr, n_bins=n_bins)
        if len(y_arr) >= n_bins
        else None
    )

    # 3. Vintage (optional)
    vintage_pts: list[VintagePoint] = []
    if (
        origination_col is not None
        and default_month_col is not None
        and origination_col in holdout_df.columns
        and default_month_col in holdout_df.columns
    ):
        df_for_vintage = holdout_df[[origination_col, default_month_col]].copy()
        df_for_vintage['defaulted'] = y_arr
        vintage_pts = compute_vintage_curves(
            df_for_vintage,
            origination_col=origination_col,
            default_col='defaulted',
            default_month_col=default_month_col,
            max_months_on_book=24,
        )

    # 4. Stress test
    stress_results: list[ScenarioResult] = []
    if (
        current_balance_col in holdout_df.columns
        and credit_limit_col in holdout_df.columns
    ):
        stress_results = run_stress_test(
            holdout_df,
            predict_pd_fn=predict_pd_fn,
            current_balance_col=current_balance_col,
            credit_limit_col=credit_limit_col,
            scenarios=STANDARD_SCENARIOS,
            base_lgd=base_lgd,
            draw_factor=draw_factor,
        )

    # 5. Loss decomposition under baseline scenario
    loss_df: pd.DataFrame | None = None
    if (
        current_balance_col in holdout_df.columns
        and credit_limit_col in holdout_df.columns
    ):
        holdout_with_pd = holdout_df.copy()
        holdout_with_pd['_pd_for_loss'] = pd_arr
        loss_df = loss_components_frame(
            holdout_with_pd,
            pd_col='_pd_for_loss',
            current_balance_col=current_balance_col,
            credit_limit_col=credit_limit_col,
            lgd=base_lgd,
            draw_factor=draw_factor,
        )

    # 6. Model card
    perf = PerformanceMetrics(
        gini=discr.gini if discr else None,
        ks_statistic=discr.ks_statistic if discr else None,
        auc=discr.auc if discr else None,
        brier_score=calib.brier_score if calib else None,
        expected_calibration_error=calib.weighted_calibration_error if calib else None,
        hl_p_value=calib.hl_p_value if calib else None,
    )
    stress_summary = [
        StressTestSummary(
            scenario_name=r.scenario_name,
            expected_loss=r.expected_loss,
            loss_rate_pct=r.loss_rate * 100,
            weighted_avg_pd=r.weighted_avg_pd,
        )
        for r in stress_results
    ]
    training_sha = hashlib.sha256(
        f'{model_name}|{model_version}|{master_seed}'.encode()
    ).hexdigest()
    card_inputs = ModelCardInputs(
        model_name=model_name,
        model_version=model_version,
        mlflow_run_id=mlflow_run_id,
        master_seed=master_seed,
        training_parquet_sha256=training_sha,
        n_training_rows=len(holdout_df),
        n_features=len(feature_cols),
        feature_cols=feature_cols,
        champion_alias=champion_alias,
        performance=perf,
        fairness=[],  # populated by bias_monitor in serving, not training
        stress_test=stress_summary,
        monotonicity_constraints=monotonicity_constraints or {},
        adverse_action_reason_codes=adverse_action_reason_codes or {},
    )
    card_md = render_model_card_markdown(card_inputs)
    sr_md = render_sr_11_7_checklist_markdown(card_inputs)

    return PostTrainingEvalResult(
        discrimination=discr,
        calibration=calib,
        vintage_points=vintage_pts,
        stress_test=stress_results,
        loss_decomposition=loss_df,
        model_card_markdown=card_md,
        sr_11_7_checklist_markdown=sr_md,
    )


def log_to_mlflow(
    result: PostTrainingEvalResult,
    mlflow_client: Any,
    run_id: str,
) -> None:
    """Write all evaluation artifacts to an MLflow run.

    Caller passes the MLflow client + run_id; we write structured artifacts
    under standard paths so downstream code (model registry UI, automated
    governance reviewers, retraining flow's promote logic) finds them.

    Artifact paths:
      - model_card.md
      - sr_11_7_checklist.md
      - eval/discrimination.json
      - eval/calibration.json
      - eval/vintage.csv
      - eval/stress_test.csv
      - eval/loss_decomposition.csv
    """
    import json
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # Model card + SR-11-7 checklist (top-level artifacts; surfaced in MLflow UI)
        (tmp / 'model_card.md').write_text(result.model_card_markdown, encoding='utf-8')
        (tmp / 'sr_11_7_checklist.md').write_text(
            result.sr_11_7_checklist_markdown, encoding='utf-8'
        )
        mlflow_client.log_artifact(run_id, str(tmp / 'model_card.md'))
        mlflow_client.log_artifact(run_id, str(tmp / 'sr_11_7_checklist.md'))

        # Eval subdirectory
        eval_dir = tmp / 'eval'
        eval_dir.mkdir()

        if result.discrimination is not None:
            (eval_dir / 'discrimination.json').write_text(
                json.dumps(
                    {
                        'auc': result.discrimination.auc,
                        'gini': result.discrimination.gini,
                        'ks_statistic': result.discrimination.ks_statistic,
                        'ks_threshold': result.discrimination.ks_threshold,
                        'top_decile_capture_rate': result.discrimination.top_decile_capture_rate,
                        'n_observations': result.discrimination.n_observations,
                        'n_events': result.discrimination.n_events,
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )

        if result.calibration is not None:
            (eval_dir / 'calibration.json').write_text(
                json.dumps(
                    {
                        'brier_score': result.calibration.brier_score,
                        'hl_statistic': result.calibration.hl_statistic,
                        'hl_p_value': result.calibration.hl_p_value,
                        'hl_df': result.calibration.hl_df,
                        'weighted_calibration_error': result.calibration.weighted_calibration_error,
                        'n_bins': result.calibration.n_bins,
                        'n_observations': result.calibration.n_observations,
                    },
                    indent=2,
                ),
                encoding='utf-8',
            )

        if result.vintage_points:
            from training_flow.vintage import vintage_curves_to_dataframe

            vintage_curves_to_dataframe(result.vintage_points).to_csv(
                eval_dir / 'vintage.csv',
                index=False,
            )

        if result.stress_test:
            from training_flow.stress_test import stress_test_to_dataframe

            stress_test_to_dataframe(result.stress_test).to_csv(
                eval_dir / 'stress_test.csv',
                index=False,
            )

        if result.loss_decomposition is not None:
            result.loss_decomposition.to_csv(
                eval_dir / 'loss_decomposition.csv',
                index=False,
            )

        # Recursively log everything under tmp as artifacts under `eval/`
        mlflow_client.log_artifacts(run_id, str(eval_dir), 'eval')
