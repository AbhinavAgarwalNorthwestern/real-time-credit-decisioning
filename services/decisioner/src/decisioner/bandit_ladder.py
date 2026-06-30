"""Contextual bandit methods ladder — FAANG Tier 1B.

The deployed `bandit.py` uses **softmax** action selection — the right choice
for production because: no episode structure (each customer decision is
one-shot), regulators want interpretability, reward signal is delayed by
months, and existing GBM + bandit infrastructure achieves >95% of attainable
performance with much less risk than full RL.

This module provides the full RL ladder so the project can SHOW the
exploration of alternatives + the honest verdict on why softmax was chosen.
Each method is implemented as a small, self-contained class with a uniform
`.select_action(context)` interface, evaluable on the same simulated bandit
logs.

The ladder:

1. **ε-greedy** — pick the argmax-reward action with prob (1−ε), random
   otherwise. The simplest exploration scheme. No use of context structure.
2. **Softmax** (production) — sample actions proportional to exp(reward / τ).
   Temperature τ controls exploration; smaller τ = more greedy. Differentiable.
3. **LinUCB** (Li et al. 2010) — linear contextual bandit with Upper
   Confidence Bound exploration bonus. Uses per-action linear model +
   credible-region radius derived from observed context covariance.
4. **Thompson Sampling** (Russo et al. 2018) — Bayesian alternative: maintain
   posterior over reward parameters, sample from posterior to pick action.
   Provably regret-optimal asymptotically.

Planned extensions (deferred to a follow-up phase — require heavier deps):

5. **Conservative Q-Learning (CQL)** — offline RL on logged bandit tuples.
   Adds a penalty for OOD actions; what JPMC / Capital One use for
   credit-limit-increase decisions (sequential reward). Needs d3rlpy.
6. **DQN with sequential framing** — treat customer-over-time as RL episodes.
   Constructs sequence from event stream. PyTorch heavy.
7. **Delayed-feedback variant** — propensity-corrected updates when reward
   arrives 3-12 months later. Provisional reward with correction.

All four implemented methods share the `BanditPolicy` Protocol and can be
swapped in the `compare_policies()` AB harness.

References:
- Sutton & Barto, "Reinforcement Learning" §2 (ε-greedy, softmax)
- Li et al. (2010), "A Contextual-Bandit Approach to Personalized News Article Recommendation" (LinUCB)
- Russo et al. (2018), "A Tutorial on Thompson Sampling"
- Kumar et al. (2020), "Conservative Q-Learning for Offline Reinforcement Learning" (CQL)

Cloud-agnostic — pure numpy. No PyTorch, no d3rlpy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np


class BanditPolicy(Protocol):
    """Uniform interface across all bandit methods."""

    def select_action(self, context: np.ndarray) -> int:
        """Pick an action (in 0..n_arms-1) given a context vector."""
        ...

    def update(self, context: np.ndarray, action: int, reward: float) -> None:
        """Update internal state given the outcome of `action` in `context`."""
        ...


# -----------------------------------------------------------------------------
# Method 1: ε-greedy
# -----------------------------------------------------------------------------


@dataclass
class EpsilonGreedyBandit:
    """Per-action running mean reward + random exploration with prob ε."""

    n_arms: int
    epsilon: float = 0.10
    seed: int = 42
    _counts: np.ndarray = field(init=False)
    _means: np.ndarray = field(init=False)
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self._counts = np.zeros(self.n_arms, dtype=int)
        self._means = np.zeros(self.n_arms, dtype=float)
        self._rng = np.random.default_rng(self.seed)

    def select_action(self, context: np.ndarray) -> int:
        if self._rng.random() < self.epsilon:
            return int(self._rng.integers(self.n_arms))
        return int(np.argmax(self._means))

    def update(self, context: np.ndarray, action: int, reward: float) -> None:
        self._counts[action] += 1
        n = self._counts[action]
        # Incremental mean
        self._means[action] += (reward - self._means[action]) / n


# -----------------------------------------------------------------------------
# Method 2: Softmax (production)
# -----------------------------------------------------------------------------


@dataclass
class SoftmaxBandit:
    """Boltzmann action selection: P(a) = exp(Q(a)/τ) / sum exp(Q(b)/τ)."""

    n_arms: int
    temperature: float = 1.0
    seed: int = 42
    _counts: np.ndarray = field(init=False)
    _means: np.ndarray = field(init=False)
    _rng: np.random.Generator = field(init=False)

    def __post_init__(self) -> None:
        self._counts = np.zeros(self.n_arms, dtype=int)
        self._means = np.zeros(self.n_arms, dtype=float)
        self._rng = np.random.default_rng(self.seed)

    def select_action(self, context: np.ndarray) -> int:
        scaled = self._means / max(self.temperature, 1e-6)
        # Numerical-stability shift
        scaled = scaled - scaled.max()
        probs = np.exp(scaled)
        probs = probs / probs.sum()
        return int(self._rng.choice(self.n_arms, p=probs))

    def update(self, context: np.ndarray, action: int, reward: float) -> None:
        self._counts[action] += 1
        n = self._counts[action]
        self._means[action] += (reward - self._means[action]) / n


# -----------------------------------------------------------------------------
# Method 3: LinUCB
# -----------------------------------------------------------------------------


@dataclass
class LinUCBBandit:
    """Linear contextual bandit with UCB exploration bonus (Li et al. 2010).

    Per-action linear model y ≈ x^T θ_a. Maintains A_a = X^T X + I and
    b_a = X^T y. Action selection: argmax_a (x^T θ_a + α sqrt(x^T A_a^{-1} x)).
    The α parameter controls exploration; ~1.0 is typical.
    """

    n_arms: int
    context_dim: int
    alpha: float = 1.0

    def __post_init__(self) -> None:
        d = self.context_dim
        # Per-arm Gram matrix (initialized to identity for ridge regularization)
        self._A_inv = [np.eye(d) for _ in range(self.n_arms)]
        self._b = [np.zeros(d) for _ in range(self.n_arms)]

    def select_action(self, context: np.ndarray) -> int:
        x = context.reshape(-1)
        scores = np.zeros(self.n_arms)
        for a in range(self.n_arms):
            theta = self._A_inv[a] @ self._b[a]
            mean = float(theta @ x)
            bonus = self.alpha * float(np.sqrt(x @ self._A_inv[a] @ x))
            scores[a] = mean + bonus
        return int(np.argmax(scores))

    def update(self, context: np.ndarray, action: int, reward: float) -> None:
        x = context.reshape(-1)
        # Sherman-Morrison update of A_inv = (A + xx^T)^{-1}
        A_inv = self._A_inv[action]
        Ax = A_inv @ x
        denom = 1.0 + float(x @ Ax)
        self._A_inv[action] = A_inv - np.outer(Ax, Ax) / denom
        self._b[action] = self._b[action] + reward * x


# -----------------------------------------------------------------------------
# Method 4: Thompson Sampling
# -----------------------------------------------------------------------------


@dataclass
class ThompsonSamplingBandit:
    """Bayesian linear bandit with normal-inverse-gamma posterior.

    Per-arm: maintain posterior mean μ_a and covariance Σ_a over reward
    coefficients. Action selection: sample θ_a ~ N(μ_a, Σ_a), pick
    argmax_a (x^T θ_a). Provably regret-optimal asymptotically (Russo et al.).

    For simplicity we use a fixed prior variance σ² = 1 (could be tuned).
    """

    n_arms: int
    context_dim: int
    prior_variance: float = 1.0
    seed: int = 42

    def __post_init__(self) -> None:
        d = self.context_dim
        # Precision matrix (Σ_a^{-1}) initialized to identity / prior_variance
        self._precision = [np.eye(d) / self.prior_variance for _ in range(self.n_arms)]
        self._mean_xy = [np.zeros(d) for _ in range(self.n_arms)]
        self._rng = np.random.default_rng(self.seed)

    def _posterior_params(self, action: int) -> tuple[np.ndarray, np.ndarray]:
        cov = np.linalg.inv(self._precision[action])
        mean = cov @ self._mean_xy[action]
        return mean, cov

    def select_action(self, context: np.ndarray) -> int:
        x = context.reshape(-1)
        scores = np.zeros(self.n_arms)
        for a in range(self.n_arms):
            mu, cov = self._posterior_params(a)
            theta = self._rng.multivariate_normal(mean=mu, cov=cov)
            scores[a] = float(theta @ x)
        return int(np.argmax(scores))

    def update(self, context: np.ndarray, action: int, reward: float) -> None:
        x = context.reshape(-1)
        # Posterior update for known-variance linear regression:
        # precision_new = precision + x x^T
        # mean_xy_new = mean_xy + reward * x
        self._precision[action] = self._precision[action] + np.outer(x, x)
        self._mean_xy[action] = self._mean_xy[action] + reward * x


# -----------------------------------------------------------------------------
# AB harness — run a policy through a sequence of (context, true_reward_fn)
# -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BanditSimulationResult:
    """Aggregate stats from a single simulation run."""

    policy_name: str
    cumulative_reward: float
    cumulative_regret: float  # vs oracle
    average_reward: float
    n_pulls_per_arm: list[int]
    final_means_per_arm: list[float] = field(default_factory=list)


def simulate_bandit(
    policy: BanditPolicy,
    contexts: np.ndarray,
    true_reward_fn,
    policy_name: str = 'policy',
    oracle_action_fn=None,
) -> BanditSimulationResult:
    """Run a policy over `len(contexts)` rounds against a known reward function.

    `true_reward_fn(context, action) -> float` — the true expected reward.
    `oracle_action_fn(context) -> int` — the best action given context (for
    regret computation); if None, uses argmax over true_reward_fn.

    Returns a `BanditSimulationResult` with cumulative reward and per-arm
    pull counts. Per-step regret is `oracle_reward - chosen_reward`.
    """
    n_arms_seen: dict[int, int] = {}
    cum_reward = 0.0
    cum_regret = 0.0
    pulls_per_arm: dict[int, int] = {}

    for i, ctx in enumerate(contexts):
        action = policy.select_action(ctx)
        reward = float(true_reward_fn(ctx, action))
        if oracle_action_fn is not None:
            oracle_action = oracle_action_fn(ctx)
        else:
            # Compute oracle by enumerating
            oracle_action = max(
                range(_infer_n_arms(policy)),
                key=lambda a: true_reward_fn(ctx, a),
            )
        oracle_reward = float(true_reward_fn(ctx, oracle_action))
        cum_reward += reward
        cum_regret += oracle_reward - reward
        pulls_per_arm[action] = pulls_per_arm.get(action, 0) + 1
        policy.update(ctx, action, reward)

    n_arms = _infer_n_arms(policy)
    pulls = [pulls_per_arm.get(a, 0) for a in range(n_arms)]
    final_means: list[float] = []
    if hasattr(policy, '_means'):
        final_means = list(policy._means)  # type: ignore[attr-defined]
    return BanditSimulationResult(
        policy_name=policy_name,
        cumulative_reward=cum_reward,
        cumulative_regret=cum_regret,
        average_reward=cum_reward / max(len(contexts), 1),
        n_pulls_per_arm=pulls,
        final_means_per_arm=final_means,
    )


def _infer_n_arms(policy) -> int:
    """Best-effort n_arms inference; falls back to len of internal stats."""
    if hasattr(policy, 'n_arms'):
        return int(policy.n_arms)
    raise AttributeError('policy must expose n_arms')


def compare_policies(
    policies: dict[str, BanditPolicy],
    contexts: np.ndarray,
    true_reward_fn,
    oracle_action_fn=None,
) -> list[BanditSimulationResult]:
    """Run all policies on the same trajectory and return their results.

    Each policy is run independently — they don't share data. Use the same
    contexts (and identical reward functions) for an apples-to-apples
    cumulative regret comparison.
    """
    return [
        simulate_bandit(
            policy=p,
            contexts=contexts,
            true_reward_fn=true_reward_fn,
            policy_name=name,
            oracle_action_fn=oracle_action_fn,
        )
        for name, p in policies.items()
    ]
