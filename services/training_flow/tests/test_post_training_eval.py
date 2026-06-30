"""Unit tests for post-training evaluation orchestrator."""

from __future__ import annotations

import numpy as np
import pandas as pd
from training_flow.post_training_eval import (
    PostTrainingEvalResult,
    run_full_eval,
)


def _synthetic_holdout(
    n: int = 500, seed: int = 42
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    """Build a synthetic OOT holdout with all needed columns."""
    rng = np.random.default_rng(seed=seed)
    df = pd.DataFrame(
        {
            'customer_id': [f'c{i}' for i in range(n)],
            'credit_score': rng.uniform(500, 850, size=n),
            'annual_income': rng.uniform(30000, 150000, size=n),
            'utilization': rng.uniform(0.1, 0.7, size=n),
            'avg_utilization_30d': rng.uniform(0.1, 0.7, size=n),
            'prev_delinquency_count': rng.integers(0, 5, size=n).astype(float),
            'paydown_rate_30d': rng.uniform(0.0, 1.0, size=n),
            'current_balance': rng.uniform(100, 4000, size=n),
            'credit_limit': rng.uniform(1000, 10000, size=n),
        }
    )
    # Predicted PD inversely related to credit score
    predicted_pd = np.clip(
        0.05 + 0.0005 * (1000 - df['credit_score'].to_numpy()), 0.001, 0.999
    )
    # Actual default loosely follows predicted (with noise to simulate model error)
    p_noisy = np.clip(predicted_pd + rng.normal(0, 0.05, size=n), 0.001, 0.999)
    actual = rng.binomial(1, p_noisy).astype(int)
    return df, predicted_pd, actual


def _trivial_pd_fn(df: pd.DataFrame) -> np.ndarray:
    """Simple PD predictor used by stress_test in tests."""
    return np.clip(0.05 + 0.0005 * (1000 - df['credit_score'].to_numpy()), 0.001, 0.999)


def test_run_full_eval_returns_result_dataclass() -> None:
    df, pred, actual = _synthetic_holdout(n=400)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        feature_cols=('credit_score', 'utilization'),
        model_name='test_model',
        model_version='1.0.0',
    )
    assert isinstance(result, PostTrainingEvalResult)


def test_run_full_eval_populates_discrimination_when_both_classes_present() -> None:
    df, pred, actual = _synthetic_holdout(n=400)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        feature_cols=('credit_score',),
    )
    assert result.discrimination is not None
    assert -1.0 <= result.discrimination.gini <= 1.0
    assert 0.0 <= result.discrimination.ks_statistic <= 1.0


def test_run_full_eval_populates_calibration() -> None:
    df, pred, actual = _synthetic_holdout(n=400)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    assert result.calibration is not None
    assert result.calibration.brier_score is not None


def test_run_full_eval_skips_vintage_when_columns_missing() -> None:
    """No origination/default_month columns → vintage_points empty, no crash."""
    df, pred, actual = _synthetic_holdout(n=200)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        origination_col=None,
        default_month_col=None,
    )
    assert result.vintage_points == []


def test_run_full_eval_runs_stress_scenarios() -> None:
    df, pred, actual = _synthetic_holdout(n=400)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    assert len(result.stress_test) == 3
    names = [s.scenario_name for s in result.stress_test]
    assert 'baseline' in names
    assert 'severely_adverse' in names


def test_run_full_eval_renders_model_card() -> None:
    df, pred, actual = _synthetic_holdout(n=300)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
        feature_cols=('credit_score', 'utilization'),
        model_name='credit_t_learner',
        model_version='1.1.0',
        master_seed=42,
        monotonicity_constraints={'credit_score': 'DECREASING'},
    )
    assert '# Model Card' in result.model_card_markdown
    assert 'credit_t_learner' in result.model_card_markdown
    assert '1.1.0' in result.model_card_markdown


def test_run_full_eval_renders_sr_11_7_checklist() -> None:
    df, pred, actual = _synthetic_holdout(n=200)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    assert 'SR-11-7 Sign-off Checklist' in result.sr_11_7_checklist_markdown
    assert 'Conceptual Soundness' in result.sr_11_7_checklist_markdown


def test_run_full_eval_skips_stress_when_balance_columns_missing() -> None:
    """Stress test needs balance/limit columns; skipped gracefully if absent."""
    df_minimal = pd.DataFrame(
        {
            'customer_id': ['c1', 'c2', 'c3'],
            'credit_score': [600.0, 700, 800],
        }
    )
    pred = np.array([0.15, 0.10, 0.05])
    actual = np.array([0, 0, 1])
    result = run_full_eval(
        df_minimal,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='nope_not_here',
        credit_limit_col='also_missing',
    )
    assert result.stress_test == []
    assert result.loss_decomposition is None


def test_run_full_eval_loss_decomposition_has_ead() -> None:
    df, pred, actual = _synthetic_holdout(n=300)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    assert result.loss_decomposition is not None
    assert 'ead' in result.loss_decomposition.columns
    assert 'expected_loss' in result.loss_decomposition.columns


def test_log_to_mlflow_writes_expected_artifacts() -> None:
    """log_to_mlflow calls the client's log_artifact / log_artifacts methods.

    Uses a stub client to verify the call pattern without needing a real
    MLflow server.
    """
    from training_flow.post_training_eval import log_to_mlflow

    class StubClient:
        def __init__(self) -> None:
            self.logged_files: list[str] = []
            self.logged_dirs: list[tuple[str, str | None]] = []

        def log_artifact(self, run_id: str, local_path: str) -> None:
            self.logged_files.append(local_path)

        def log_artifacts(
            self, run_id: str, local_dir: str, artifact_path: str | None = None
        ) -> None:
            self.logged_dirs.append((local_dir, artifact_path))

    df, pred, actual = _synthetic_holdout(n=200)
    result = run_full_eval(
        df,
        pred,
        actual,
        predict_pd_fn=_trivial_pd_fn,
        current_balance_col='current_balance',
        credit_limit_col='credit_limit',
    )
    client = StubClient()
    log_to_mlflow(result, client, run_id='test-run-id')

    # Should have logged the model card + checklist as top-level artifacts
    assert any('model_card.md' in p for p in client.logged_files)
    assert any('sr_11_7_checklist.md' in p for p in client.logged_files)
    # Should have logged the eval/ subdir
    assert len(client.logged_dirs) >= 1
