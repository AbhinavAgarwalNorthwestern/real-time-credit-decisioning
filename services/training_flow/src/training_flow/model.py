"""D2-4 — Per-segment neural T-learner architecture.

Each segment in {0..5} gets its own T-learner. Each T-learner has two
arms (control μ₀ and treated μ₁); uplift = μ₁(x) - μ₀(x). Each arm
predicts three outputs jointly so the bandit can compose expected
profit downstream:

  - p_accept   : sigmoid head (binary)
  - delta_spend: linear head (continuous; clipped at 0)
  - p_default  : sigmoid head (binary)

Architecture choice: shallow MLP (3 hidden layers, GELU, dropout 0.1).
Small per-segment cohort (~150 customers × N decision points) doesn't
support a deep network; per-segment fit + small capacity prevents
overfitting and keeps ONNX export trivial.

Extensible: replace MLP body with a TransformerBody by subclassing
`ArmBackbone` — the three-head output structure stays the same so the
bandit downstream is unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch import nn


@dataclass(frozen=True, slots=True)
class ModelConfig:
    n_features: int
    hidden_dim: int = 64
    n_hidden_layers: int = 3
    dropout: float = 0.1


class ArmBackbone(nn.Module):
    """Shared trunk producing a hidden representation."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        layers: list[nn.Module] = [nn.Linear(cfg.n_features, cfg.hidden_dim), nn.GELU()]
        for _ in range(cfg.n_hidden_layers - 1):
            layers += [
                nn.Linear(cfg.hidden_dim, cfg.hidden_dim),
                nn.GELU(),
                nn.Dropout(cfg.dropout),
            ]
        self.body = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.body(x)  # type: ignore[no-any-return]


class TLearnerArm(nn.Module):
    """One arm (control or treated). Three output heads."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.backbone = ArmBackbone(cfg)
        self.head_p_accept = nn.Linear(cfg.hidden_dim, 1)
        self.head_delta_spend = nn.Linear(cfg.hidden_dim, 1)
        self.head_p_default = nn.Linear(cfg.hidden_dim, 1)

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        h = self.backbone(x)
        return {
            'p_accept': torch.sigmoid(self.head_p_accept(h)).squeeze(-1),
            'delta_spend': torch.relu(self.head_delta_spend(h)).squeeze(-1),
            'p_default': torch.sigmoid(self.head_p_default(h)).squeeze(-1),
        }


class SegmentTLearner(nn.Module):
    """Two-arm T-learner for one segment. Uplift = treated - control."""

    def __init__(self, cfg: ModelConfig) -> None:
        super().__init__()
        self.control = TLearnerArm(cfg)
        self.treated = TLearnerArm(cfg)
        self.cfg = cfg

    def forward(self, x: torch.Tensor) -> dict[str, torch.Tensor]:
        c = self.control(x)
        t = self.treated(x)
        return {
            'control_p_accept': c['p_accept'],
            'control_delta_spend': c['delta_spend'],
            'control_p_default': c['p_default'],
            'treated_p_accept': t['p_accept'],
            'treated_delta_spend': t['delta_spend'],
            'treated_p_default': t['p_default'],
        }
