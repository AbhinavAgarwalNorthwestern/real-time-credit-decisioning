"""Pipeline-level test: exercises D2-2 through D2-6 in offline mode.

Generates synthetic features inline (no K8s, no RisingWave, no backfill),
then runs DGP validation, baselines, training (2 trials only for speed),
ONNX export, and MLflow logging against a local file-based tracking URI.

Marked `pipeline` — skipped by default in `uv run pytest`; run explicitly
with `uv run pytest -m pipeline` or in CI's pipeline-test job.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from training_flow.baselines import evaluate, make_baselines
from training_flow.customer_params_loader import load_cohort_from_generator
from training_flow.export import export_segment
from training_flow.label_simulator import CustomerParams, RowContext, simulate_label
from training_flow.mlflow_log import derive_seed
from training_flow.model import ModelConfig, SegmentTLearner
from training_flow.train import TrainingRecipe, train_per_segment
from training_flow.validate_dgp import run_data_gate

MASTER_SEED = 42
COHORT_SIZE = 200
N_ROWS_PER_CUSTOMER = 20


def _generate_offline_features(
    cohort: dict[str, CustomerParams], seed: int
) -> pd.DataFrame:
    """Produce synthetic features without RisingWave — pure numpy."""
    rng = np.random.default_rng(seed)
    rows: list[dict] = []
    for cust in cohort.values():
        for _ in range(N_ROWS_PER_CUSTOMER):
            utilization = rng.uniform(0.1, 0.9)
            velocity_24h = rng.exponential(3.0 + cust.segment_id * 1.5)
            rows.append(
                {
                    'customer_id': cust.customer_id,
                    'segment_id': cust.segment_id,
                    'as_of': pd.Timestamp('2026-06-01')
                    + pd.Timedelta(minutes=rng.integers(0, 10080)),
                    'velocity_5m': velocity_24h * rng.uniform(0.05, 0.3),
                    'total_spend_5m': rng.exponential(20.0),
                    'avg_spend_5m': rng.exponential(15.0),
                    'utilization': utilization,
                    'velocity_1h': velocity_24h * rng.uniform(0.1, 0.5),
                    'total_spend_1h': rng.exponential(50.0),
                    'avg_spend_1h': rng.exponential(30.0),
                    'mcc_entropy_1h': rng.uniform(0.5, 2.5),
                    'velocity_24h': velocity_24h,
                    'total_spend_24h': rng.exponential(200.0),
                    'avg_spend_24h': rng.exponential(50.0),
                    'pct_late_night_24h': rng.uniform(0.0, 0.4)
                    + cust.segment_id * 0.05,
                    'avg_interarrival_24h': rng.exponential(3600.0),
                    'velocity_7d': velocity_24h * 5 + rng.normal(0, 2),
                    'total_spend_7d': rng.exponential(800.0),
                    'geo_variance_7d': rng.exponential(0.02 + cust.segment_id * 0.01),
                    'velocity_30d': velocity_24h * 20 + rng.normal(0, 5),
                    'total_spend_30d': rng.exponential(3000.0),
                    'paydown_rate_30d': max(
                        0.0, 0.8 - cust.segment_id * 0.12 + rng.normal(0, 0.1)
                    ),
                    'pct_cash_advance_30d': max(
                        0.0, cust.segment_id * 0.03 + rng.normal(0, 0.02)
                    ),
                    'avg_utilization_30d': utilization + rng.normal(0, 0.05),
                }
            )
    return pd.DataFrame(rows)


def _attach_labels_offline(
    df: pd.DataFrame, cohort: dict[str, CustomerParams], seed: int
) -> pd.DataFrame:
    """Simulate labels inline."""
    rng = np.random.default_rng(seed)
    treated, accepted, spend_delta, defaulted, profit = [], [], [], [], []
    for _, row in df.iterrows():
        cust = cohort[str(row['customer_id'])]
        ctx = RowContext(
            utilization=float(row['utilization']),
            velocity_24h=float(row['velocity_24h']),
            paydown_rate_30d=float(row['paydown_rate_30d']),
        )
        lbl = simulate_label(cust, ctx, rng)
        treated.append(lbl.treated)
        accepted.append(lbl.accepted)
        spend_delta.append(lbl.spend_delta)
        defaulted.append(lbl.defaulted)
        profit.append(lbl.profit)
    df = df.copy()
    df['T'] = treated
    df['accepted'] = accepted
    df['spend_delta'] = spend_delta
    df['defaulted'] = defaulted
    df['profit'] = profit
    return df


@pytest.fixture(scope='module')
def labelled_df() -> tuple[pd.DataFrame, dict[str, CustomerParams]]:
    """Module-scoped fixture: generate features + labels once."""
    cohort = load_cohort_from_generator(cohort_size=COHORT_SIZE, seed=MASTER_SEED)
    features = _generate_offline_features(
        cohort, seed=derive_seed(MASTER_SEED, 'offline_features')
    )
    df = _attach_labels_offline(
        features, cohort, seed=derive_seed(MASTER_SEED, 'labels')
    )
    return df, cohort


@pytest.mark.pipeline
def test_dgp_validation_runs(
    labelled_df: tuple[pd.DataFrame, dict[str, CustomerParams]],
) -> None:
    """DGP gate runs without error on synthetic offline data."""
    df, _ = labelled_df
    results = run_data_gate(df)
    assert len(results) == 3
    for r in results:
        assert r.name in (
            'rate_heterogeneity',
            'segment_separability',
            'temporal_signal',
        )


@pytest.mark.pipeline
def test_baselines_run(
    labelled_df: tuple[pd.DataFrame, dict[str, CustomerParams]],
) -> None:
    """All 4 baselines produce reports without error."""
    df, cohort = labelled_df
    feature_cols = [
        c
        for c in df.columns
        if c
        not in {
            'customer_id',
            'as_of',
            'T',
            'accepted',
            'spend_delta',
            'defaulted',
            'profit',
            'segment_id',
        }
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    baselines = make_baselines(df, feature_cols)
    reports = [evaluate(b, df, cohort) for b in baselines]
    assert len(reports) == 4
    for r in reports:
        assert r.mean_simulated_profit is not None


@pytest.mark.pipeline
def test_training_runs(
    labelled_df: tuple[pd.DataFrame, dict[str, CustomerParams]],
) -> None:
    """Per-segment training completes with 2 Optuna trials (speed)."""
    df, cohort = labelled_df
    feature_cols = [
        c
        for c in df.columns
        if c
        not in {
            'customer_id',
            'as_of',
            'T',
            'accepted',
            'spend_delta',
            'defaulted',
            'profit',
            'segment_id',
        }
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    recipe = TrainingRecipe(
        feature_cols=feature_cols,
        seed=derive_seed(MASTER_SEED, 'train'),
        n_optuna_trials=2,
    )
    results = train_per_segment(df, cohort, recipe)
    assert len(results) >= 1
    for seg_id, sr in results.items():
        assert sr.segment_id == seg_id
        assert sr.model_state_dict is not None


@pytest.mark.pipeline
def test_onnx_export(
    labelled_df: tuple[pd.DataFrame, dict[str, CustomerParams]], tmp_path: Path
) -> None:
    """ONNX export + numerical equivalence for one segment."""
    df, cohort = labelled_df
    feature_cols = [
        c
        for c in df.columns
        if c
        not in {
            'customer_id',
            'as_of',
            'T',
            'accepted',
            'spend_delta',
            'defaulted',
            'profit',
            'segment_id',
        }
        and pd.api.types.is_numeric_dtype(df[c])
    ]
    seg_id = int(df['segment_id'].iloc[0])
    seg_df = df[df['segment_id'] == seg_id]

    cfg = ModelConfig(n_features=len(feature_cols), hidden_dim=32, n_hidden_layers=2)
    model = SegmentTLearner(cfg)
    holdout = seg_df[feature_cols].fillna(0.0).to_numpy()[:100].astype(np.float32)
    result = export_segment(seg_id, model, holdout, tmp_path / 'onnx')
    assert result.passed
    assert result.onnx_path.exists()


@pytest.mark.pipeline
def test_mlflow_log_offline(
    labelled_df: tuple[pd.DataFrame, dict[str, CustomerParams]], tmp_path: Path
) -> None:
    """MLflow logging works against a local file:// tracking URI."""
    from training_flow.mlflow_log import log_pipeline
    from training_flow.validate_dgp import run_data_gate

    df, cohort = labelled_df
    feature_cols = [
        c
        for c in df.columns
        if c
        not in {
            'customer_id',
            'as_of',
            'T',
            'accepted',
            'spend_delta',
            'defaulted',
            'profit',
            'segment_id',
        }
        and pd.api.types.is_numeric_dtype(df[c])
    ]

    dgp_results = run_data_gate(df)
    baselines = make_baselines(df, feature_cols)
    baseline_reports = [evaluate(b, df, cohort) for b in baselines]

    recipe = TrainingRecipe(
        feature_cols=feature_cols,
        seed=derive_seed(MASTER_SEED, 'train'),
        n_optuna_trials=2,
    )
    segment_results = train_per_segment(df, cohort, recipe)

    onnx_exports = []
    for seg_id, sr in segment_results.items():
        cfg = ModelConfig(
            n_features=len(feature_cols),
            hidden_dim=int(sr.best_params.get('hidden_dim', 32)),
            n_hidden_layers=int(sr.best_params.get('n_hidden_layers', 2)),
            dropout=float(sr.best_params.get('dropout', 0.1)),
        )
        m = SegmentTLearner(cfg)
        m.load_state_dict(sr.model_state_dict)
        seg_df = df[df['segment_id'] == seg_id]
        holdout = seg_df[feature_cols].fillna(0.0).to_numpy()[:100].astype(np.float32)
        if len(holdout) == 0:
            continue
        er = export_segment(seg_id, m, holdout, tmp_path / 'onnx')
        onnx_exports.append(er)

    parquet_path = tmp_path / 'training.parquet'
    df.to_parquet(parquet_path, index=False)

    result = log_pipeline(
        parquet_path=parquet_path,
        feature_cols=feature_cols,
        dgp_results=dgp_results,
        segment_results=segment_results,
        baseline_reports=baseline_reports,
        onnx_exports=onnx_exports,
        recipe=recipe,
        master_seed=MASTER_SEED,
        split_fractions={'train': 0.7, 'val': 0.15, 'test': 0.15},
        policy_thresholds={'offer_cli_uplift_threshold': 0.0},
        artefact_dir=tmp_path / 'artefacts',
        tracking_uri=f'file://{tmp_path / "mlruns"}',
        experiment_name='test_pipeline',
        run_name='test_run',
    )
    assert result.parent_run_id
    assert result.registered_model_version >= 1
    assert result.champion_variant in (
        'neural',
        'baseline_logistic_t_learner',
        'baseline_always_offer',
        'baseline_never_offer',
        'baseline_random_50_50',
    )
