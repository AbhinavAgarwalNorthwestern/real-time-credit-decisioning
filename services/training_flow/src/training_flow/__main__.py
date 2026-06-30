"""CLI entrypoint — end-to-end Day-2 training run.

Usage (devcontainer):
    uv run python -m training_flow --master-seed 42 --backfill-days 7

Master seed flows to:
  - producer backfill (via backfill_trigger)
  - synthetic-RCT label simulation
  - PyTorch + numpy + random in train.py
  - Optuna sampler
  - sklearn random_state in baselines
all via derive_seed(master_seed, namespace). Same master_seed → bit-
identical run end-to-end.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import numpy as np
from loguru import logger

from .baselines import evaluate, make_baselines
from .config import TrainingFlowSettings
from .customer_params_loader import load_cohort_from_generator
from .data_builder import build
from .export import export_segment
from .mlflow_log import derive_seed, log_pipeline
from .train import TrainingRecipe, train_per_segment
from .validate_dgp import assert_data_gate_passes, run_data_gate

POLICY_THRESHOLDS = {
    'offer_cli_uplift_threshold': 0.0,
    'min_kendall_tau': 0.6,
    'min_segment_separability_auc': 0.85,
}

SPLIT_FRACTIONS = {'train': 0.7, 'val': 0.15, 'test': 0.15}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description='Day-2 training pipeline')
    p.add_argument('--master-seed', type=int, default=42)
    p.add_argument('--backfill-days', type=int, default=7)
    p.add_argument('--output-parquet', type=Path, default=Path('data/training.parquet'))
    p.add_argument('--artefact-dir', type=Path, default=Path('artefacts/day2'))
    p.add_argument(
        '--mlflow-tracking-uri',
        type=str,
        default=os.environ.get('MLFLOW_TRACKING_URI', 'http://localhost:5001'),
    )
    p.add_argument('--n-optuna-trials', type=int, default=20)
    p.add_argument(
        '--skip-backfill',
        action='store_true',
        help='Skip backfill Job (use existing MV data)',
    )
    return p.parse_args()


def main() -> int:
    pipeline_start = time.monotonic()
    args = _parse_args()

    logger.info(
        f'Pipeline start | seed={args.master_seed} backfill_days={args.backfill_days} '
        f'trials={args.n_optuna_trials} skip_backfill={args.skip_backfill}'
    )

    settings = TrainingFlowSettings(
        backfill_days=args.backfill_days,
        backfill_seed=derive_seed(args.master_seed, 'backfill'),
        label_seed=derive_seed(args.master_seed, 'labels'),
        output_path=args.output_parquet,
    )

    # ── Phase 1: Build training parquet (D2-1b) ──────────────────────────
    t0 = time.monotonic()
    logger.info('[1/6] data_builder: backfill → poll MVs → join → labels → parquet')
    build_result = build(settings, skip_backfill=args.skip_backfill)
    logger.info(
        f'[1/6] done | rows={build_result.n_rows} '
        f'customers={build_result.n_customers} elapsed={time.monotonic() - t0:.1f}s'
    )

    # ── Phase 2: DGP validation gate (D2-2) ──────────────────────────────
    import pandas as pd

    t0 = time.monotonic()
    logger.info(
        '[2/6] validate_dgp: rate heterogeneity, segment separability, temporal signal'
    )
    df = pd.read_parquet(settings.output_path)
    logger.info(f'  parquet loaded: {len(df)} rows, {len(df.columns)} columns')
    dgp_results = run_data_gate(df)
    for cr in dgp_results:
        status = 'PASS' if cr.passed else 'FAIL'
        logger.info(
            f'  {status} {cr.name}: observed={cr.observed:.4f} threshold={cr.threshold}'
        )
    assert_data_gate_passes(df)
    logger.info(
        f'[2/6] done | all_passed={all(cr.passed for cr in dgp_results)} '
        f'elapsed={time.monotonic() - t0:.1f}s'
    )

    # ── Phase 3: Baselines (D2-3) ────────────────────────────────────────
    t0 = time.monotonic()
    logger.info(
        '[3/6] baselines: always-offer, never-offer, random-50/50, logistic T-learner'
    )
    cohort = load_cohort_from_generator(cohort_size=1000, seed=settings.backfill_seed)
    min_coverage = 0.5
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
        and not c.startswith('as_of_')
        and pd.api.types.is_numeric_dtype(df[c])
        and df[c].notna().mean() >= min_coverage
    ]
    logger.info(
        f'  {len(feature_cols)} features selected (≥{min_coverage:.0%} coverage)'
    )
    # ── Group split: OOT holdout by customer_id ────────────────────────
    all_customers = df['customer_id'].unique()
    split_rng = np.random.RandomState(derive_seed(args.master_seed, 'split'))
    split_rng.shuffle(all_customers)
    n_test = max(1, int(len(all_customers) * SPLIT_FRACTIONS['test']))
    oot_custs = set(all_customers[:n_test])
    df_oot = df[df['customer_id'].isin(oot_custs)].copy()
    df_dev = df[~df['customer_id'].isin(oot_custs)].copy()
    logger.info(
        f'  group split: {len(df_dev)} dev rows ({len(all_customers) - n_test} customers), '
        f'{len(df_oot)} OOT rows ({n_test} customers)'
    )

    baselines = make_baselines(df_dev, feature_cols)
    baseline_reports = [evaluate(b, df_dev, cohort) for b in baselines]
    for br in baseline_reports:
        tau_str = (
            f'{br.kendall_tau_vs_true_uplift:.4f}'
            if br.kendall_tau_vs_true_uplift
            else 'N/A'
        )
        logger.info(
            f'  {br.name}: profit={br.mean_simulated_profit:.4f} '
            f'agreement={br.optimal_agreement_rate:.4f} tau={tau_str}'
        )
    logger.info(f'[3/6] done | elapsed={time.monotonic() - t0:.1f}s')

    # ── Phase 4: Neural per-segment training (D2-4) ──────────────────────
    t0 = time.monotonic()
    logger.info(
        f'[4/6] train_per_segment: 6 segments × {args.n_optuna_trials} Optuna trials'
    )
    recipe = TrainingRecipe(
        feature_cols=feature_cols,
        seed=derive_seed(args.master_seed, 'train'),
        n_optuna_trials=args.n_optuna_trials,
    )
    segment_results = train_per_segment(df_dev, cohort, recipe)
    for seg_id, sr in sorted(segment_results.items()):
        logger.info(
            f'  segment {seg_id}: tau={sr.val_kendall_tau:.4f} params={sr.best_params}'
        )
    mean_tau = sum(s.val_kendall_tau for s in segment_results.values()) / max(
        len(segment_results), 1
    )
    logger.info(
        f'[4/6] done | segments={len(segment_results)} '
        f'mean_tau={mean_tau:.4f} elapsed={time.monotonic() - t0:.1f}s'
    )

    # ── Phase 5: ONNX export + numerical equivalence (D2-5) ─────────────
    t0 = time.monotonic()
    logger.info('[5/6] onnx_export: per-segment ONNX + ORT equivalence (tol < 1e-5)')
    onnx_dir = args.artefact_dir / 'onnx'
    from .model import ModelConfig, SegmentTLearner

    onnx_exports = []
    for seg_id, sr in segment_results.items():
        cfg = ModelConfig(
            n_features=len(feature_cols),
            hidden_dim=int(sr.best_params.get('hidden_dim', 64)),
            n_hidden_layers=int(sr.best_params.get('n_hidden_layers', 3)),
            dropout=float(sr.best_params.get('dropout', 0.1)),
        )
        m = SegmentTLearner(cfg)
        m.load_state_dict(sr.model_state_dict)
        seg_df = df_dev[df_dev['segment_id'] == seg_id]
        holdout = seg_df[feature_cols].fillna(0.0).to_numpy()[:1000].astype(np.float32)
        if len(holdout) == 0:
            logger.warning(f'  segment {seg_id}: skipped (empty)')
            continue
        er = export_segment(seg_id, m, holdout, onnx_dir)
        onnx_exports.append(er)
        logger.info(
            f'  segment {seg_id}: max_diff={er.max_abs_diff:.2e} passed={er.passed}'
        )
    logger.info(
        f'[5/6] done | exported={len(onnx_exports)} '
        f'all_passed={all(e.passed for e in onnx_exports)} '
        f'elapsed={time.monotonic() - t0:.1f}s'
    )

    # ── Phase 6: MLflow logging + model registry (D2-6) ──────────────────
    t0 = time.monotonic()
    logger.info(
        '[6/6] mlflow_log: parent run + children + RegisteredModel + champion alias'
    )
    result = log_pipeline(
        parquet_path=settings.output_path,
        feature_cols=feature_cols,
        dgp_results=dgp_results,
        segment_results=segment_results,
        baseline_reports=baseline_reports,
        onnx_exports=onnx_exports,
        recipe=recipe,
        master_seed=args.master_seed,
        split_fractions=SPLIT_FRACTIONS,
        policy_thresholds=POLICY_THRESHOLDS,
        artefact_dir=args.artefact_dir,
        tracking_uri=args.mlflow_tracking_uri,
    )
    logger.info(
        f'[6/6] done | run={result.parent_run_id} '
        f'version={result.registered_model_version} '
        f'champion={result.champion_variant} elapsed={time.monotonic() - t0:.1f}s'
    )

    # ── Summary ──────────────────────────────────────────────────────────
    total_s = time.monotonic() - pipeline_start
    logger.info(
        f'Pipeline complete | seed={args.master_seed} rows={build_result.n_rows} '
        f'segments={len(segment_results)} champion={result.champion_variant} '
        f'total={total_s:.1f}s'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
