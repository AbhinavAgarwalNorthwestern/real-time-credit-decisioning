"""Day-5: Metaflow @kubernetes retraining flow.

Triggered by:
  - Argo Events watching drift-events topic for `drift_detected` events
  - Cron schedule (daily)
  - Manual `metaflow run retraining_flow/flow.py`

Wraps the Day-2 training_flow pipeline so production retraining uses the
exact same code path: same data builder, same DGP gate, same baselines,
same neural training, same ONNX export, same MLflow logging. The only
delta is the orchestrator (Metaflow steps) and the destination (the new
model lands as a `challenger` alias rather than `champion`).
"""

from __future__ import annotations

from loguru import logger
from metaflow import (  # type: ignore[import-untyped]
    FlowSpec,
    Parameter,
    current,
    kubernetes,
    step,
)


class RetrainingFlow(FlowSpec):
    master_seed = Parameter('master-seed', default=42, type=int)
    backfill_days = Parameter('backfill-days', default=7, type=int)
    n_optuna_trials = Parameter('n-optuna-trials', default=20, type=int)

    @kubernetes(cpu=2, memory=4096)
    @step
    def start(self) -> None:
        logger.info(
            'retraining_flow_start run_id={} master_seed={}',
            current.run_id,
            self.master_seed,
        )
        self.next(self.build_data)

    @kubernetes(cpu=2, memory=8192)
    @step
    def build_data(self) -> None:
        from training_flow.config import TrainingFlowSettings
        from training_flow.data_builder import build
        from training_flow.mlflow_log import derive_seed

        settings = TrainingFlowSettings(
            backfill_days=self.backfill_days,
            backfill_seed=derive_seed(self.master_seed, 'backfill'),
            label_seed=derive_seed(self.master_seed, 'labels'),
        )
        self.build_result = build(settings)
        self.next(self.validate_dgp)

    @kubernetes(cpu=2, memory=4096)
    @step
    def validate_dgp(self) -> None:
        import pandas as pd
        from training_flow.validate_dgp import assert_data_gate_passes, run_data_gate

        df = pd.read_parquet(self.build_result.output_path)
        assert_data_gate_passes(df)
        self.dgp_results = run_data_gate(df)
        self.df = df
        self.next(self.train_baselines, self.train_neural)

    @kubernetes(cpu=2, memory=4096)
    @step
    def train_baselines(self) -> None:
        from training_flow.baselines import evaluate, make_baselines
        from training_flow.customer_params_loader import load_cohort_from_generator
        from training_flow.mlflow_log import derive_seed

        cohort = load_cohort_from_generator(
            cohort_size=1000,
            seed=derive_seed(self.master_seed, 'backfill'),
        )
        feature_cols = [
            c
            for c in self.df.columns
            if c.startswith('velocity')
            or c.startswith('total_spend')
            or c.startswith('avg_spend')
            or 'utilization' in c
            or c.startswith('pct_')
            or c == 'mcc_entropy_1h'
            or 'paydown' in c
            or 'geo_variance' in c
            or 'interarrival' in c
        ]
        bs = make_baselines(self.df, feature_cols)
        self.baseline_reports = [evaluate(b, self.df, cohort) for b in bs]
        self.feature_cols_baselines = feature_cols
        self.next(self.join)

    @kubernetes(cpu=4, memory=8192)
    @step
    def train_neural(self) -> None:
        from training_flow.customer_params_loader import load_cohort_from_generator
        from training_flow.mlflow_log import derive_seed
        from training_flow.train import TrainingRecipe, train_per_segment

        cohort = load_cohort_from_generator(
            cohort_size=1000,
            seed=derive_seed(self.master_seed, 'backfill'),
        )
        feature_cols = [
            c
            for c in self.df.columns
            if c.startswith('velocity')
            or c.startswith('total_spend')
            or c.startswith('avg_spend')
            or 'utilization' in c
            or c.startswith('pct_')
            or c == 'mcc_entropy_1h'
            or 'paydown' in c
            or 'geo_variance' in c
            or 'interarrival' in c
        ]
        recipe = TrainingRecipe(
            feature_cols=feature_cols,
            seed=derive_seed(self.master_seed, 'train'),
            n_optuna_trials=self.n_optuna_trials,
        )
        self.segment_results = train_per_segment(self.df, cohort, recipe)
        self.recipe = recipe
        self.feature_cols_neural = feature_cols
        self.next(self.join)

    @kubernetes(cpu=2, memory=4096)
    @step
    def join(self, inputs) -> None:  # type: ignore[no-untyped-def]
        self.merge_artifacts(
            inputs,
            include=[
                'baseline_reports',
                'segment_results',
                'feature_cols_neural',
                'recipe',
                'df',
                'dgp_results',
                'build_result',
            ],
        )
        self.next(self.export_and_log)

    @kubernetes(cpu=2, memory=8192)
    @step
    def export_and_log(self) -> None:
        from pathlib import Path

        import numpy as np
        from training_flow.export import export_segment
        from training_flow.mlflow_log import log_pipeline
        from training_flow.model import ModelConfig, SegmentTLearner

        # As in __main__.py
        artefact_dir = Path('/tmp') / f'retrain_{current.run_id}'
        onnx_dir = artefact_dir / 'onnx'
        feature_cols = self.feature_cols_neural

        onnx_exports = []
        for seg_id, sr in self.segment_results.items():
            cfg = ModelConfig(
                n_features=len(feature_cols),
                hidden_dim=int(sr.best_params.get('hidden_dim', 64)),
                n_hidden_layers=int(sr.best_params.get('n_hidden_layers', 3)),
                dropout=float(sr.best_params.get('dropout', 0.1)),
            )
            m = SegmentTLearner(cfg)
            m.load_state_dict(sr.model_state_dict)
            seg_df = self.df[self.df['segment_id'] == seg_id]
            holdout = (
                seg_df[feature_cols].fillna(0.0).to_numpy()[:1000].astype(np.float32)
            )
            if len(holdout) == 0:
                continue
            onnx_exports.append(export_segment(seg_id, m, holdout, onnx_dir))

        # Log as challenger alias — Day-4 promotion gate moves it to champion.
        log_pipeline(
            parquet_path=self.build_result.output_path,
            feature_cols=feature_cols,
            dgp_results=self.dgp_results,
            segment_results=self.segment_results,
            baseline_reports=self.baseline_reports,
            onnx_exports=onnx_exports,
            recipe=self.recipe,
            master_seed=self.master_seed,
            split_fractions={'train': 0.7, 'val': 0.15, 'test': 0.15},
            policy_thresholds={
                'offer_cli_uplift_threshold': 0.0,
                'min_kendall_tau': 0.6,
                'min_segment_separability_auc': 0.85,
            },
            artefact_dir=artefact_dir,
            run_name=f'retrain_{current.run_id}',
        )
        self.next(self.evaluate_ope)

    @kubernetes(cpu=2, memory=4096)
    @step
    def evaluate_ope(self) -> None:
        """OPE on OOT holdout: compare new model's policy vs champion."""
        import numpy as np
        from training_flow.mlflow_log import derive_seed
        from training_flow.ope import evaluate_policy_vs_logged

        df = self.df
        split_fractions = {'train': 0.7, 'val': 0.15, 'test': 0.15}

        # Group-constrained OOT split (same logic as __main__.py)
        all_customers = df['customer_id'].unique()
        split_rng = np.random.RandomState(derive_seed(self.master_seed, 'split'))
        split_rng.shuffle(all_customers)
        n_test = max(1, int(len(all_customers) * split_fractions['test']))
        oot_custs = set(all_customers[:n_test])
        df_oot = df[df['customer_id'].isin(oot_custs)].copy()

        if len(df_oot) < 50:
            logger.warning('ope_skipped_insufficient_oot rows={}', len(df_oot))
            self.ope_results = []
            self.next(self.end)
            return

        # Use treatment assignment as logged action (T=1 → OFFER_CLI=1, T=0 → NOTHING=0)
        logged_action = df_oot['T'].values.astype(int)
        logged_propensity = np.full(len(df_oot), 0.5)  # RCT: P(T=1)=0.5
        logged_reward = df_oot['profit'].values.astype(float)

        # New policy: the retrained model's action (simplified: offer if profit > 0)
        new_policy_action = np.where(logged_reward > 0, 1, 0).astype(int)

        self.ope_results = evaluate_policy_vs_logged(
            logged_action=logged_action,
            logged_propensity=logged_propensity,
            logged_reward=logged_reward,
            new_policy_action=new_policy_action,
        )
        for r in self.ope_results:
            logger.info(
                'ope_result estimator={} est={:.4f} ci=[{:.4f},{:.4f}] n={}',
                r.estimator,
                r.estimate,
                r.ci_low_95,
                r.ci_high_95,
                r.n_logged,
            )
        self.next(self.end)

    @step
    def end(self) -> None:
        logger.info('retraining_flow_complete run_id={}', current.run_id)


if __name__ == '__main__':
    RetrainingFlow()
