"""D2-4 — Per-segment T-learner training loop with Optuna HPO.

For each segment in [0..5]:
  - Split rows for that segment into control (T=0) and treated (T=1)
  - Fit μ₀ on control, μ₁ on treated
  - Each is a SegmentTLearner.control / .treated arm (in practice trained
    jointly with arm-specific loss masking — see _train_one_segment)
  - Optuna picks lr, hidden_dim, dropout, weight_decay
  - Best model per segment is selected on holdout uplift Kendall τ
    against analytical true uplift (the truest validation metric for
    synthetic data)

Seeds pinned for reproducibility (per `feedback_code_hygiene` memory).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import optuna
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

from . import get_logger
from .label_simulator import CustomerParams
from .model import ModelConfig, SegmentTLearner

log = get_logger(__name__)


@dataclass
class TrainingRecipe:
    feature_cols: list[str]
    target_cols: tuple[str, str, str] = ('accepted', 'spend_delta', 'defaulted')
    batch_size: int = 256
    max_epochs: int = 50
    early_stopping_patience: int = 5
    seed: int = 42
    n_optuna_trials: int = 20


@dataclass
class SegmentTrainResult:
    segment_id: int
    best_params: dict[str, float | int]
    val_kendall_tau: float
    val_loss: float
    model_state_dict: dict[str, torch.Tensor] = field(default_factory=dict)
    n_optuna_trials_completed: int = 0
    all_trial_params: list[dict[str, float | int]] = field(default_factory=list)
    all_trial_values: list[float] = field(default_factory=list)


def _pin_seeds(seed: int) -> None:
    """Pin everything that could introduce non-determinism."""
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def _multi_head_loss(
    pred: dict[str, torch.Tensor],
    target_accepted: torch.Tensor,
    target_spend: torch.Tensor,
    target_default: torch.Tensor,
    arm: str,  # 'control' or 'treated'
) -> torch.Tensor:
    """Joint loss across three heads for one arm. BCE for binary heads,
    MSE (scale-corrected) for spend."""
    bce = torch.nn.BCELoss()
    mse = torch.nn.MSELoss()
    return (
        bce(pred[f'{arm}_p_accept'], target_accepted.float())
        + mse(pred[f'{arm}_delta_spend'] / 1000.0, target_spend / 1000.0)
        + bce(pred[f'{arm}_p_default'], target_default.float())
    )


def _make_loader(
    df: pd.DataFrame, feature_cols: list[str], batch_size: int
) -> DataLoader:
    X = torch.tensor(df[feature_cols].fillna(0.0).to_numpy(), dtype=torch.float32)
    T = torch.tensor(df['T'].to_numpy(), dtype=torch.long)
    a = torch.tensor(df['accepted'].to_numpy(), dtype=torch.float32)
    s = torch.tensor(df['spend_delta'].to_numpy(), dtype=torch.float32)
    d = torch.tensor(df['defaulted'].to_numpy(), dtype=torch.float32)
    return DataLoader(
        TensorDataset(X, T, a, s, d),
        batch_size=batch_size,
        shuffle=True,
    )


def _train_one_segment(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    recipe: TrainingRecipe,
    trial: optuna.Trial,
    cohort: dict[str, CustomerParams],
) -> tuple[SegmentTLearner, float]:
    """One Optuna trial. Returns (best model, val Kendall τ)."""
    _pin_seeds(recipe.seed + trial.number)

    cfg = ModelConfig(
        n_features=len(recipe.feature_cols),
        hidden_dim=trial.suggest_categorical('hidden_dim', [32, 64, 128]),
        n_hidden_layers=trial.suggest_int('n_hidden_layers', 2, 4),
        dropout=trial.suggest_float('dropout', 0.0, 0.3),
    )
    lr = trial.suggest_float('lr', 1e-4, 1e-2, log=True)
    weight_decay = trial.suggest_float('weight_decay', 1e-6, 1e-3, log=True)

    model = SegmentTLearner(cfg)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    loader = _make_loader(train_df, recipe.feature_cols, recipe.batch_size)

    best_tau = -1.0
    patience = recipe.early_stopping_patience
    for epoch in range(recipe.max_epochs):
        model.train()
        total = 0.0
        for X, T, a, s, d in loader:
            opt.zero_grad()
            pred = model(X)
            # Mask loss to the arm the row's T selects.
            ctrl_mask = T == 0
            treat_mask = T == 1
            loss = torch.tensor(0.0)
            if ctrl_mask.any():
                loss = loss + _multi_head_loss(
                    {k: v[ctrl_mask] for k, v in pred.items()},
                    a[ctrl_mask],
                    s[ctrl_mask],
                    d[ctrl_mask],
                    'control',
                )
            if treat_mask.any():
                loss = loss + _multi_head_loss(
                    {k: v[treat_mask] for k, v in pred.items()},
                    a[treat_mask],
                    s[treat_mask],
                    d[treat_mask],
                    'treated',
                )
            loss.backward()
            opt.step()
            total += float(loss.item())

        # Validation: predicted uplift vs true uplift Kendall τ
        tau = _validate_uplift(model, val_df, recipe.feature_cols, cohort)
        log.info(
            'train_epoch',
            trial=trial.number,
            epoch=epoch,
            loss=total / max(len(loader), 1),
            val_tau=tau,
        )
        if tau > best_tau:
            best_tau = tau
            patience = recipe.early_stopping_patience
        else:
            patience -= 1
            if patience <= 0:
                break

    return model, best_tau


def _validate_uplift(
    model: SegmentTLearner,
    val_df: pd.DataFrame,
    feature_cols: list[str],
    cohort: dict[str, CustomerParams],
) -> float:
    """Kendall τ between predicted and analytical true uplift."""
    from scipy.stats import kendalltau

    from .baselines import _true_uplift_per_row

    model.eval()
    with torch.no_grad():
        X = torch.tensor(
            val_df[feature_cols].fillna(0.0).to_numpy(), dtype=torch.float32
        )
        pred = model(X)
    # Predicted profit per arm — composed identically to label_simulator.
    # Annualized revenue under a $5000 exposure assumption (proxy; real
    # decisioner uses the customer's actual exposure).
    revenue = pred['treated_delta_spend'] * 12 * 0.12 * 0.95
    loss = pred['treated_p_default'] * 5000.0 * 0.80
    capital = 5000.0 * 0.0064
    pred_profit_treated = (
        pred['treated_p_accept'] * (revenue - loss - capital)
    ).numpy()
    pred_uplift = pred_profit_treated  # control = 0 by simulator definition

    true_uplift = np.array(
        [_true_uplift_per_row(r, cohort) for _, r in val_df.iterrows()],
        dtype=float,
    )
    tau, _ = kendalltau(pred_uplift, true_uplift)
    return float(tau) if not np.isnan(tau) else 0.0


def _group_temporal_split(
    sub: pd.DataFrame,
    seed: int,
    val_frac: float = 0.2,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split by customer_id so no customer appears in both train and val.
    When `as_of` column exists and concept drift is present, uses temporal
    cutoff within each customer's timeline; currently uses group shuffle
    (stationary DGP)."""
    customers = sub['customer_id'].unique()
    rng = np.random.RandomState(seed)
    rng.shuffle(customers)
    n_val = max(1, int(len(customers) * val_frac))
    val_custs = set(customers[:n_val])
    val_df = sub[sub['customer_id'].isin(val_custs)]
    train_df = sub[~sub['customer_id'].isin(val_custs)]
    return train_df, val_df


def train_per_segment(
    df: pd.DataFrame,
    cohort: dict[str, CustomerParams],
    recipe: TrainingRecipe,
) -> dict[int, SegmentTrainResult]:
    """Top-level entry — loops segments [0..5], runs Optuna HPO per segment."""
    results: dict[int, SegmentTrainResult] = {}

    for segment_id in sorted(df['segment_id'].dropna().unique()):
        sub = df[df['segment_id'] == segment_id].copy()
        train_df, val_df = _group_temporal_split(sub, recipe.seed)
        log.info(
            'segment_train_start',
            segment_id=int(segment_id),
            n_train=len(train_df),
            n_val=len(val_df),
        )

        best_model: SegmentTLearner | None = None
        best_params: dict[str, float | int] = {}
        best_tau = -1.0

        def objective(
            trial: optuna.Trial,
            _train_df: pd.DataFrame = train_df,
            _val_df: pd.DataFrame = val_df,
        ) -> float:
            nonlocal best_model, best_params, best_tau
            model, tau = _train_one_segment(
                _train_df,
                _val_df,
                recipe,
                trial,
                cohort,
            )
            if tau > best_tau:
                best_tau = tau
                best_model = model
                best_params = dict(trial.params)
            return tau

        study = optuna.create_study(
            direction='maximize',
            sampler=optuna.samplers.TPESampler(seed=recipe.seed),
        )
        study.optimize(
            objective, n_trials=recipe.n_optuna_trials, show_progress_bar=False
        )

        assert best_model is not None
        trial_params = [dict(t.params) for t in study.trials]
        trial_values = [
            t.value if t.value is not None else float('nan') for t in study.trials
        ]
        results[int(segment_id)] = SegmentTrainResult(
            segment_id=int(segment_id),
            best_params=best_params,
            val_kendall_tau=best_tau,
            val_loss=0.0,
            model_state_dict={
                k: v.detach().clone() for k, v in best_model.state_dict().items()
            },
            n_optuna_trials_completed=len(study.trials),
            all_trial_params=trial_params,
            all_trial_values=trial_values,
        )
        log.info(
            'segment_train_done',
            segment_id=int(segment_id),
            best_tau=best_tau,
            best_params=best_params,
        )

    return results
