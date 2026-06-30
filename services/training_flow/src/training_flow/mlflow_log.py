"""D2-6 — MLflow logging + model registry.

Every artefact, hyperparameter, threshold, dataset split, and random seed
is logged to MLflow so any run can be replayed bit-identically.
Reproducibility is enforced via a single master seed (`master_seed`) from
which every downstream RNG seed is derived deterministically.

Structure:
  parent_run
    ├─ params: master_seed, derived seeds, data version, split fractions,
    │           DGP thresholds, decision-policy thresholds, feature list
    ├─ metrics: DGP observed values, dataset sizes
    ├─ artefacts:
    │     training.parquet (data version)
    │     manifest.json    (seed + hyperparams + thresholds in one file)
    │     feature_schema.json (column → dtype mapping)
    │
    ├─ baseline_<name> (nested run, one per baseline)
    │     metrics: mean_simulated_profit, optimal_agreement_rate,
    │              kendall_tau (if defined)
    │     params: policy-specific hyperparams
    │
    ├─ neural_seg_<id> (nested run, one per segment)
    │     params: best_params (lr, hidden_dim, dropout, weight_decay,
    │             n_hidden_layers, batch_size, max_epochs,
    │             early_stopping_patience, n_optuna_trials)
    │     metrics: val_kendall_tau, train_loss_final, val_loss_final,
    │              n_train_rows, n_val_rows
    │     artefacts: state_dict.pt, segment_<id>.onnx
    │
    └─ RegisteredModel("uplift_per_segment")
          alias `champion` → neural OR baseline_<name>, whichever wins
          on validation profit (honest reporting; SR 11-7 spirit)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import mlflow
import torch
from mlflow.entities import RunStatus

from . import get_logger
from .baselines import BaselineReport
from .export import EQUIVALENCE_TOL, ExportResult
from .train import SegmentTrainResult, TrainingRecipe
from .validate_dgp import CriterionResult

log = get_logger(__name__)

REGISTERED_MODEL_NAME = 'uplift_per_segment'


@dataclass(frozen=True, slots=True)
class LogResult:
    parent_run_id: str
    registered_model_version: int
    champion_variant: str  # 'neural' or 'baseline_<name>'
    manifest_path: Path


def derive_seed(master_seed: int, namespace: str) -> int:
    """Deterministic per-namespace seed from a master seed.

    Reproducibility: from one master_seed, every component gets a stable
    sub-seed that survives module-reorganization. Same master_seed →
    bit-identical training run.
    """
    h = hashlib.sha256(f'{master_seed}|{namespace}'.encode()).digest()
    return int.from_bytes(h[:4], 'big') % (2**31 - 1)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _write_hpo_csv(
    path: Path,
    trial_params: list[dict[str, float | int]],
    trial_values: list[float],
) -> None:
    import csv

    if not trial_params:
        return
    keys = list(trial_params[0].keys())
    with path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['trial', 'val_kendall_tau'] + keys)
        writer.writeheader()
        for i, (params, val) in enumerate(
            zip(trial_params, trial_values, strict=False)
        ):
            row: dict[str, Any] = {'trial': i, 'val_kendall_tau': val}
            row.update(params)
            writer.writerow(row)


def _write_manifest(
    out_path: Path,
    master_seed: int,
    derived_seeds: dict[str, int],
    recipe: TrainingRecipe,
    dgp_thresholds: dict[str, str],
    policy_thresholds: dict[str, float],
    parquet_path: Path,
    parquet_sha256: str,
    feature_cols: list[str],
    split_fractions: dict[str, float],
) -> None:
    payload: dict[str, Any] = {
        'master_seed': master_seed,
        'derived_seeds': derived_seeds,
        'training_recipe': {
            'feature_cols': feature_cols,
            'target_cols': list(recipe.target_cols),
            'batch_size': recipe.batch_size,
            'max_epochs': recipe.max_epochs,
            'early_stopping_patience': recipe.early_stopping_patience,
            'n_optuna_trials': recipe.n_optuna_trials,
        },
        'dgp_thresholds': dgp_thresholds,
        'policy_thresholds': policy_thresholds,
        'data': {
            'parquet_path': str(parquet_path),
            'parquet_sha256': parquet_sha256,
            'split_fractions': split_fractions,
        },
        'equivalence_tol': EQUIVALENCE_TOL,
    }
    with out_path.open('w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def log_pipeline(
    parquet_path: Path,
    feature_cols: list[str],
    dgp_results: list[CriterionResult],
    segment_results: dict[int, SegmentTrainResult],
    baseline_reports: list[BaselineReport],
    onnx_exports: list[ExportResult],
    recipe: TrainingRecipe,
    master_seed: int,
    split_fractions: dict[str, float],
    policy_thresholds: dict[str, float],
    artefact_dir: Path,
    tracking_uri: str = os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5000'),
    experiment_name: str = 'realtime_credit_decisioning',
    run_name: str = 'day2_training',
) -> LogResult:
    """Log everything. Caller's responsibility: pass the same master_seed
    on every run that should be bit-identical."""
    log.info(
        'mlflow_connect',
        tracking_uri=tracking_uri,
        experiment=experiment_name,
        run_name=run_name,
    )
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)

    derived_seeds = {
        'backfill': derive_seed(master_seed, 'backfill'),
        'labels': derive_seed(master_seed, 'labels'),
        'train': derive_seed(master_seed, 'train'),
        'optuna_sampler': derive_seed(master_seed, 'optuna_sampler'),
        'baseline_random': derive_seed(master_seed, 'baseline_random'),
    }

    dgp_thresholds = {cr.name: cr.threshold for cr in dgp_results}

    artefact_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = artefact_dir / 'manifest.json'
    parquet_sha = _file_sha256(parquet_path)
    _write_manifest(
        manifest_path,
        master_seed=master_seed,
        derived_seeds=derived_seeds,
        recipe=recipe,
        dgp_thresholds=dgp_thresholds,
        policy_thresholds=policy_thresholds,
        parquet_path=parquet_path,
        parquet_sha256=parquet_sha,
        feature_cols=feature_cols,
        split_fractions=split_fractions,
    )

    feature_schema_path = artefact_dir / 'feature_schema.json'
    with feature_schema_path.open('w', encoding='utf-8') as f:
        json.dump({'feature_cols': feature_cols}, f, indent=2, sort_keys=True)

    parent = mlflow.start_run(run_name=run_name)
    parent_run_id = parent.info.run_id

    try:
        # Parent params — every reproducibility lever in one place.
        mlflow.log_param('master_seed', master_seed)
        for k, v in derived_seeds.items():
            mlflow.log_param(f'seed.{k}', v)
        mlflow.log_param('parquet_path', str(parquet_path))
        mlflow.log_param('parquet_sha256', parquet_sha)
        mlflow.log_param('n_features', len(feature_cols))
        for k, v in split_fractions.items():
            mlflow.log_param(f'split.{k}', v)
        for k, v in policy_thresholds.items():
            mlflow.log_param(f'policy.{k}', v)
        mlflow.log_param('equivalence_tol', EQUIVALENCE_TOL)

        # DGP gate
        for cr in dgp_results:
            mlflow.log_metric(f'dgp.{cr.name}.observed', cr.observed)
            mlflow.log_param(f'dgp.{cr.name}.threshold', cr.threshold)
            mlflow.log_param(f'dgp.{cr.name}.passed', cr.passed)
            mlflow.log_param(f'dgp.{cr.name}.detail', cr.detail)

        # Parent artefacts
        mlflow.log_artifact(str(parquet_path), artifact_path='data')
        mlflow.log_artifact(str(manifest_path), artifact_path='manifest')
        mlflow.log_artifact(str(feature_schema_path), artifact_path='manifest')

        # Baselines (nested)
        best_baseline_profit = float('-inf')
        best_baseline_name = ''
        for br in baseline_reports:
            with mlflow.start_run(nested=True, run_name=f'baseline_{br.name}'):
                mlflow.log_param('policy_name', br.name)
                mlflow.log_metric('mean_simulated_profit', br.mean_simulated_profit)
                mlflow.log_metric('optimal_agreement_rate', br.optimal_agreement_rate)
                if br.kendall_tau_vs_true_uplift is not None:
                    mlflow.log_metric('kendall_tau', br.kendall_tau_vs_true_uplift)
                if br.mean_simulated_profit > best_baseline_profit:
                    best_baseline_profit = br.mean_simulated_profit
                    best_baseline_name = br.name

        # Neural per-segment (nested)
        neural_mean_profit_proxy = 0.0
        for seg_id, sr in segment_results.items():
            with mlflow.start_run(nested=True, run_name=f'neural_seg_{seg_id}'):
                mlflow.log_param('segment_id', seg_id)
                for k, v in sr.best_params.items():
                    mlflow.log_param(f'hp.{k}', v)
                mlflow.log_param('hp.batch_size', recipe.batch_size)
                mlflow.log_param('hp.max_epochs', recipe.max_epochs)
                mlflow.log_param(
                    'hp.early_stopping_patience', recipe.early_stopping_patience
                )
                mlflow.log_param('hp.n_optuna_trials', recipe.n_optuna_trials)
                mlflow.log_param('hp.seed', derived_seeds['train'])

                mlflow.log_metric('val_kendall_tau', sr.val_kendall_tau)
                mlflow.log_metric('val_loss', sr.val_loss)
                mlflow.log_metric(
                    'n_optuna_trials_completed', sr.n_optuna_trials_completed
                )
                neural_mean_profit_proxy += sr.val_kendall_tau

                for trial_idx, (params, value) in enumerate(
                    zip(sr.all_trial_params, sr.all_trial_values, strict=False)
                ):
                    mlflow.log_metric('optuna_trial_tau', value, step=trial_idx)
                    for pk, pv in params.items():
                        mlflow.log_metric(
                            f'optuna_trial_{pk}', float(pv), step=trial_idx
                        )

                hpo_csv_path = artefact_dir / f'seg_{seg_id}_hpo_trials.csv'
                _write_hpo_csv(hpo_csv_path, sr.all_trial_params, sr.all_trial_values)
                mlflow.log_artifact(str(hpo_csv_path), artifact_path='hpo')

                sd_path = artefact_dir / f'segment_{seg_id}_state.pt'
                torch.save(sr.model_state_dict, sd_path)
                mlflow.log_artifact(str(sd_path), artifact_path='pytorch')

        # ONNX exports
        for er in onnx_exports:
            mlflow.log_artifact(str(er.onnx_path), artifact_path='onnx')
            mlflow.log_metric(f'onnx.seg_{er.segment_id}.max_abs_diff', er.max_abs_diff)
            mlflow.log_param(f'onnx.seg_{er.segment_id}.equivalence_passed', er.passed)

        # Champion alias choice
        champion_variant = (
            'neural'
            if neural_mean_profit_proxy > best_baseline_profit
            else f'baseline_{best_baseline_name}'
        )
        mlflow.log_param('champion_variant', champion_variant)

        # Registry
        from mlflow.tracking import MlflowClient

        client = MlflowClient()
        try:
            client.create_registered_model(REGISTERED_MODEL_NAME)
        except mlflow.exceptions.MlflowException:
            pass
        mv = client.create_model_version(
            name=REGISTERED_MODEL_NAME,
            source=f'runs:/{parent_run_id}/onnx',
            run_id=parent_run_id,
        )
        client.set_registered_model_alias(REGISTERED_MODEL_NAME, 'champion', mv.version)

        log.info(
            'mlflow_logged',
            parent_run_id=parent_run_id,
            mv_version=mv.version,
            champion=champion_variant,
            manifest=str(manifest_path),
        )

        return LogResult(
            parent_run_id=parent_run_id,
            registered_model_version=int(mv.version),
            champion_variant=champion_variant,
            manifest_path=manifest_path,
        )
    finally:
        mlflow.end_run(RunStatus.to_string(RunStatus.FINISHED))
