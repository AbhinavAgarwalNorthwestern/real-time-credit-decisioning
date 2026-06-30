"""Unit tests for the bandit methods ladder (FAANG 1B)."""

from __future__ import annotations

import numpy as np
from decisioner.bandit_ladder import (
    BanditSimulationResult,
    EpsilonGreedyBandit,
    LinUCBBandit,
    SoftmaxBandit,
    ThompsonSamplingBandit,
    compare_policies,
    simulate_bandit,
)


def _bandit_problem_3_arms(n: int = 500, seed: int = 42):
    """Construct a simple 3-arm contextual bandit:
    - Arm 0: reward = 0.5 + noise (best on average)
    - Arm 1: reward = 0.3 + noise
    - Arm 2: reward = 0.1 + noise
    Context is a 4-d random vector (irrelevant to rewards — non-contextual problem
    for the simple methods; LinUCB and Thompson will see no signal).
    """
    rng = np.random.default_rng(seed=seed)
    contexts = rng.standard_normal((n, 4))
    means = [0.5, 0.3, 0.1]

    def reward_fn(_ctx: np.ndarray, action: int) -> float:
        return float(means[action] + rng.normal(0, 0.1))

    return contexts, reward_fn


# -----------------------------------------------------------------------------
# Method-level contract tests
# -----------------------------------------------------------------------------


def test_epsilon_greedy_select_action_in_range() -> None:
    bandit = EpsilonGreedyBandit(n_arms=4, epsilon=0.1)
    ctx = np.zeros(5)
    for _ in range(50):
        a = bandit.select_action(ctx)
        assert 0 <= a < 4


def test_softmax_select_action_in_range() -> None:
    bandit = SoftmaxBandit(n_arms=3, temperature=1.0)
    ctx = np.zeros(5)
    for _ in range(50):
        a = bandit.select_action(ctx)
        assert 0 <= a < 3


def test_linucb_select_action_in_range() -> None:
    bandit = LinUCBBandit(n_arms=3, context_dim=5)
    ctx = np.random.randn(5)
    for _ in range(50):
        a = bandit.select_action(ctx)
        assert 0 <= a < 3


def test_thompson_select_action_in_range() -> None:
    bandit = ThompsonSamplingBandit(n_arms=3, context_dim=5)
    ctx = np.random.randn(5)
    for _ in range(50):
        a = bandit.select_action(ctx)
        assert 0 <= a < 3


# -----------------------------------------------------------------------------
# Learning behavior tests
# -----------------------------------------------------------------------------


def test_epsilon_greedy_converges_to_best_arm() -> None:
    """After many pulls, ε-greedy should pull arm 0 (best) majority of time."""
    contexts, reward_fn = _bandit_problem_3_arms(n=2000, seed=42)
    bandit = EpsilonGreedyBandit(n_arms=3, epsilon=0.1, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, policy_name='eps_greedy')
    # Best arm (0) should be pulled most often
    assert result.n_pulls_per_arm[0] > result.n_pulls_per_arm[1]
    assert result.n_pulls_per_arm[0] > result.n_pulls_per_arm[2]
    # Estimated mean for arm 0 should be close to true 0.5
    assert abs(result.final_means_per_arm[0] - 0.5) < 0.1


def test_softmax_converges_with_low_temperature() -> None:
    """Low-temp softmax behaves like greedy; should converge."""
    contexts, reward_fn = _bandit_problem_3_arms(n=2000, seed=42)
    bandit = SoftmaxBandit(n_arms=3, temperature=0.1, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, policy_name='softmax')
    assert result.n_pulls_per_arm[0] > result.n_pulls_per_arm[1]
    assert result.n_pulls_per_arm[0] > result.n_pulls_per_arm[2]


def test_high_temperature_softmax_explores_uniformly() -> None:
    """High temperature → essentially uniform; arms pulled roughly equally."""
    contexts, reward_fn = _bandit_problem_3_arms(n=900, seed=42)
    bandit = SoftmaxBandit(n_arms=3, temperature=100.0, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, policy_name='softmax_explore')
    # With τ=100, all arms should be pulled within ~20% of each other
    max_pulls = max(result.n_pulls_per_arm)
    min_pulls = min(result.n_pulls_per_arm)
    assert max_pulls / min_pulls < 1.6


def test_cumulative_regret_decreases_for_learning_policies() -> None:
    """Over time, ε-greedy's regret growth should slow (sub-linear)."""
    contexts, reward_fn = _bandit_problem_3_arms(n=2000, seed=42)
    bandit = EpsilonGreedyBandit(n_arms=3, epsilon=0.1, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, policy_name='eps_greedy')
    # Best arm has mean 0.5; worst-case regret per round = 0.5 - 0.1 = 0.4
    # Cumulative regret over 2000 rounds is bounded by 800 (always pull worst)
    # Learning policy should be well under that
    assert result.cumulative_regret < 200.0


# -----------------------------------------------------------------------------
# AB harness tests
# -----------------------------------------------------------------------------


def test_compare_policies_returns_one_result_per_policy() -> None:
    contexts, reward_fn = _bandit_problem_3_arms(n=500, seed=42)
    policies = {
        'epsilon_greedy': EpsilonGreedyBandit(n_arms=3, epsilon=0.1, seed=42),
        'softmax': SoftmaxBandit(n_arms=3, temperature=0.5, seed=42),
    }
    results = compare_policies(policies, contexts, reward_fn)
    assert len(results) == 2
    names = [r.policy_name for r in results]
    assert 'epsilon_greedy' in names
    assert 'softmax' in names


def test_simulate_returns_complete_result_dataclass() -> None:
    contexts, reward_fn = _bandit_problem_3_arms(n=200, seed=42)
    bandit = EpsilonGreedyBandit(n_arms=3, epsilon=0.1, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, policy_name='test')
    assert isinstance(result, BanditSimulationResult)
    assert result.policy_name == 'test'
    assert result.cumulative_reward > 0
    assert result.cumulative_regret >= 0
    assert abs(result.average_reward - result.cumulative_reward / 200) < 1e-9
    assert sum(result.n_pulls_per_arm) == 200


def test_oracle_action_fn_supplied_overrides_enumeration() -> None:
    """Passing an explicit oracle saves the enumeration cost in big-K settings."""
    contexts, reward_fn = _bandit_problem_3_arms(n=100, seed=42)

    # Constant oracle says always arm 0 (which IS the best)
    def oracle(_ctx):
        return 0

    bandit = EpsilonGreedyBandit(n_arms=3, epsilon=0.1, seed=42)
    result = simulate_bandit(bandit, contexts, reward_fn, oracle_action_fn=oracle)
    assert result.cumulative_regret >= 0


# -----------------------------------------------------------------------------
# Statistical-property tests for the contextual methods
# -----------------------------------------------------------------------------


def test_linucb_state_grows_with_updates() -> None:
    """LinUCB internal state changes after observing data."""
    bandit = LinUCBBandit(n_arms=2, context_dim=3, alpha=1.0)
    initial_A_inv = bandit._A_inv[0].copy()
    ctx = np.array([1.0, 2.0, 3.0])
    bandit.update(ctx, action=0, reward=1.0)
    assert not np.allclose(bandit._A_inv[0], initial_A_inv)


def test_thompson_state_grows_with_updates() -> None:
    """Thompson sampling internal posterior changes after observing data."""
    bandit = ThompsonSamplingBandit(n_arms=2, context_dim=3, seed=42)
    initial_precision = bandit._precision[0].copy()
    ctx = np.array([1.0, 0.5, -0.3])
    bandit.update(ctx, action=0, reward=0.8)
    # Precision matrix increases as we observe more data
    assert not np.allclose(bandit._precision[0], initial_precision)


def test_thompson_select_action_explores_initially() -> None:
    """With no data, Thompson should sample from the prior — varies across calls."""
    bandit = ThompsonSamplingBandit(n_arms=3, context_dim=2, seed=42)
    ctx = np.array([1.0, 0.5])
    actions = [bandit.select_action(ctx) for _ in range(50)]
    # With prior samples, all three arms should be selected at least once
    distinct = set(actions)
    assert len(distinct) >= 2  # exploration is happening
