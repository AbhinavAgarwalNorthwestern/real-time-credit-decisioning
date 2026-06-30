"""Unit tests for the off-policy evaluation harness."""

from __future__ import annotations

import numpy as np
from training_flow.ope import (
    doubly_robust,
    evaluate_policy_vs_logged,
    ips,
    snips,
)

RNG = np.random.default_rng(seed=42)
N = 200


def _logged_data(n: int = N):
    actions = RNG.integers(0, 3, size=n)
    propensities = np.full(n, 1.0 / 3.0)
    rewards = RNG.normal(10.0, 2.0, size=n)
    return actions, propensities, rewards


def test_ips_all_matching() -> None:
    actions, props, rewards = _logged_data()
    result = ips(actions, props, rewards, actions)
    expected = 3.0 * float(rewards.mean())
    assert abs(result.estimate - expected) < 1e-6
    assert result.estimator == 'IPS'
    assert result.n_logged == N


def test_ips_no_matching() -> None:
    actions, props, rewards = _logged_data()
    new = (actions + 1) % 3
    result = ips(actions, props, rewards, new)
    assert result.estimate == 0.0


def test_snips_self_normalizes() -> None:
    actions, props, rewards = _logged_data()
    r_snips = snips(actions, props, rewards, actions)
    r_ips = ips(actions, props, rewards, actions)
    mean_reward = float(rewards.mean())
    assert abs(r_snips.estimate - mean_reward) < 1.0
    assert r_ips.estimate > r_snips.estimate * 2


def test_dr_perfect_outcome_model() -> None:
    actions, props, rewards = _logged_data()
    result = doubly_robust(
        actions,
        props,
        rewards,
        actions,
        outcome_model_pred_for_chosen=rewards,
        outcome_model_pred_for_logged=rewards,
    )
    mean_reward = float(rewards.mean())
    assert abs(result.estimate - mean_reward) < 0.5
    assert result.estimator == 'DR'


def test_bootstrap_ci_contains_point_estimate() -> None:
    actions, props, rewards = _logged_data()
    result = ips(actions, props, rewards, actions)
    assert result.ci_low_95 <= result.estimate <= result.ci_high_95


def test_snips_ci_contains_point_estimate() -> None:
    actions, props, rewards = _logged_data()
    result = snips(actions, props, rewards, actions)
    assert result.ci_low_95 <= result.estimate <= result.ci_high_95


def test_evaluate_without_outcome_model() -> None:
    actions, props, rewards = _logged_data()
    results = evaluate_policy_vs_logged(actions, props, rewards, actions)
    assert len(results) == 2
    assert {r.estimator for r in results} == {'IPS', 'SNIPS'}


def test_evaluate_with_outcome_model() -> None:
    actions, props, rewards = _logged_data()
    results = evaluate_policy_vs_logged(
        actions,
        props,
        rewards,
        actions,
        outcome_model_pred_for_chosen=rewards,
        outcome_model_pred_for_logged=rewards,
    )
    assert len(results) == 3
    assert {r.estimator for r in results} == {'IPS', 'SNIPS', 'DR'}
