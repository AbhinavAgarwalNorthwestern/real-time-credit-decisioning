"""Integration tests for POST /decide — the platform's hot path.

Tests the full request pipeline: feature lookup → segment routing →
champion inference → shadow scoring → bandit → adverse action → audit.
External dependencies (RisingWave, MLflow, Kafka) are replaced with
in-process fakes; the route logic is exercised end-to-end.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from decisioner.adverse_action import AdverseActionExplainer
from decisioner.audit import AuditRecord
from decisioner.feature_lookup import (
    CircuitBreaker,
    CustomerFeatures,
)
from decisioner.inference import ArmPrediction, UpliftPrediction
from decisioner.metrics_collector import MetricsCollector
from decisioner.rate_limiter import RateLimiterMiddleware
from decisioner.routes.decide import router as decide_router
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ── Constants ──────────────────────────────────────────────────────

_FEATURE_COLS = (
    'velocity_5m',
    'total_spend_5m',
    'avg_spend_5m',
    'utilization',
    'velocity_1h',
    'total_spend_1h',
    'avg_spend_1h',
    'mcc_entropy_1h',
)

_HIGH_UPLIFT = UpliftPrediction(
    control=ArmPrediction(p_accept=0.0, delta_spend=0.0, p_default=0.0),
    treated=ArmPrediction(p_accept=0.9, delta_spend=500.0, p_default=0.001),
)

_ZERO_UPLIFT = UpliftPrediction(
    control=ArmPrediction(p_accept=0.0, delta_spend=0.0, p_default=0.0),
    treated=ArmPrediction(p_accept=0.0, delta_spend=0.0, p_default=0.0),
)


# ── Fake dependencies ─────────────────────────────────────────────


class FakeAudit:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    async def emit(self, record: AuditRecord) -> None:
        self.records.append(record)


class FakeRegistry:
    def __init__(
        self,
        uplift: UpliftPrediction = _HIGH_UPLIFT,
        has_challenger: bool = False,
    ) -> None:
        self._uplift = uplift
        self._has_challenger = has_challenger
        self._feature_cols = list(_FEATURE_COLS)

    @property
    def feature_cols(self) -> list[str]:
        return self._feature_cols

    def predict(
        self,
        segment_id: int,
        features: np.ndarray,
        alias: str = 'champion',
    ) -> UpliftPrediction:
        return self._uplift

    def is_loaded(self, alias: str) -> bool:
        if alias == 'challenger':
            return self._has_challenger
        return True


def _make_features(customer_id: str = 'cust-001') -> CustomerFeatures:
    return CustomerFeatures(
        customer_id=customer_id,
        values=tuple([1.0] * len(_FEATURE_COLS)),
        segment_id=0,
    )


# ── Test app factory ──────────────────────────────────────────────


def _make_app(
    *,
    registry: FakeRegistry | None = None,
    audit: FakeAudit | None = None,
    explainer: AdverseActionExplainer | None = None,
    canary_fraction: float = 0.0,
    per_customer_rps: int = 100,
    global_rps: int = 1000,
) -> FastAPI:
    test_app = FastAPI()
    test_app.state.db_pool = MagicMock()
    test_app.state.models = registry or FakeRegistry()
    test_app.state.audit = audit or FakeAudit()
    test_app.state.metrics = MetricsCollector()
    test_app.state.canary_fraction = canary_fraction
    test_app.state.adverse_action_explainer = explainer
    test_app.add_middleware(
        RateLimiterMiddleware,
        per_customer_rps=per_customer_rps,
        global_rps=global_rps,
    )
    test_app.include_router(decide_router)
    return test_app


def _patches():
    """Context manager stack that patches feature-lookup imports in the route."""
    fetch = patch(
        'decisioner.routes.decide.fetch_one',
        new_callable=AsyncMock,
        return_value=_make_features(),
    )
    cols = patch(
        'decisioner.routes.decide.feature_column_order',
        return_value=_FEATURE_COLS,
    )
    return fetch, cols


# ── Tests: happy path ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_happy_path() -> None:
    app = _make_app(registry=FakeRegistry(_HIGH_UPLIFT))
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body['action'] == 'OFFER_CLI'
    assert body['customer_id'] == 'cust-001'
    assert body['acted_on'] == 'champion'
    assert body['propensity'] > 0.0


@pytest.mark.asyncio
async def test_denial_happy_path() -> None:
    app = _make_app(registry=FakeRegistry(_ZERO_UPLIFT))
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-002', 'fraud_score': 0.0}
            )
    assert resp.status_code == 200
    body = resp.json()
    assert body['action'] == 'NOTHING'
    assert body['expected_profit'] == 0.0


# ── Tests: error paths ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_customer_not_found_404() -> None:
    cb = CircuitBreaker()
    app = _make_app()
    with (
        patch(
            'decisioner.routes.decide.fetch_one',
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch('decisioner.routes.decide.get_circuit_breaker', return_value=cb),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'unknown', 'fraud_score': 0.0}
            )
    assert resp.status_code == 404
    assert 'not found' in resp.json()['detail']


@pytest.mark.asyncio
async def test_circuit_open_returns_503() -> None:
    cb = CircuitBreaker(failure_threshold=1)
    cb.record_failure()
    assert cb.is_open
    app = _make_app()
    with (
        patch(
            'decisioner.routes.decide.fetch_one',
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch('decisioner.routes.decide.get_circuit_breaker', return_value=cb),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    assert resp.status_code == 503
    assert 'unavailable' in resp.json()['detail']


@pytest.mark.asyncio
async def test_empty_customer_id_422() -> None:
    app = _make_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        resp = await c.post('/decide', json={'customer_id': '', 'fraud_score': 0.0})
    assert resp.status_code == 422


# ── Tests: response shape ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_response_shape() -> None:
    app = _make_app(registry=FakeRegistry(_HIGH_UPLIFT))
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    body = resp.json()
    required = {
        'decision_id',
        'customer_id',
        'action',
        'propensity',
        'expected_profit',
        'rationale',
        'acted_on',
        'latency_ms',
    }
    assert required.issubset(body.keys())
    assert isinstance(body['decision_id'], str) and len(body['decision_id']) > 0
    assert isinstance(body['propensity'], float)
    assert isinstance(body['latency_ms'], int)
    assert body['acted_on'] in ('champion', 'challenger')


# ── Tests: shadow scoring ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_shadow_scoring_latency_present() -> None:
    reg = FakeRegistry(_HIGH_UPLIFT, has_challenger=True)
    app = _make_app(registry=reg, canary_fraction=0.0)
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    body = resp.json()
    assert body['challenger_latency_ms'] is not None
    assert isinstance(body['challenger_latency_ms'], int)
    assert body['acted_on'] == 'champion'


@pytest.mark.asyncio
async def test_no_challenger_latency_when_not_loaded() -> None:
    reg = FakeRegistry(_HIGH_UPLIFT, has_challenger=False)
    app = _make_app(registry=reg)
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    body = resp.json()
    assert body['challenger_latency_ms'] is None


# ── Tests: adverse action ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_adverse_action_on_denial() -> None:
    explainer = AdverseActionExplainer(background_size=5)
    rng = np.random.default_rng(42)
    for _ in range(5):
        explainer.accumulate(rng.standard_normal(len(_FEATURE_COLS)).astype(np.float32))
    assert explainer.is_ready

    app = _make_app(
        registry=FakeRegistry(_ZERO_UPLIFT),
        explainer=explainer,
    )
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-deny', 'fraud_score': 0.0}
            )
    body = resp.json()
    assert body['action'] == 'NOTHING'
    assert 'adverse_action_reasons' in body


@pytest.mark.asyncio
async def test_no_adverse_action_on_approval() -> None:
    explainer = AdverseActionExplainer(background_size=5)
    rng = np.random.default_rng(42)
    for _ in range(5):
        explainer.accumulate(rng.standard_normal(len(_FEATURE_COLS)).astype(np.float32))

    app = _make_app(
        registry=FakeRegistry(_HIGH_UPLIFT),
        explainer=explainer,
    )
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-ok', 'fraud_score': 0.0}
            )
    body = resp.json()
    assert body['action'] == 'OFFER_CLI'
    assert body['adverse_action_reasons'] is None


# ── Tests: audit + metrics ────────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_emitted() -> None:
    audit = FakeAudit()
    app = _make_app(registry=FakeRegistry(_HIGH_UPLIFT), audit=audit)
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    assert resp.status_code == 200
    assert len(audit.records) == 1
    rec = audit.records[0]
    assert rec.customer_id == 'cust-001'
    assert rec.acted_on_alias == 'champion'
    assert len(rec.decision_id) > 0
    assert 'adverse_action_eligible' in rec.regulatory_flags


@pytest.mark.asyncio
async def test_audit_regulatory_flags_denial() -> None:
    audit = FakeAudit()
    app = _make_app(registry=FakeRegistry(_ZERO_UPLIFT), audit=audit)
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            await c.post(
                '/decide', json={'customer_id': 'cust-deny', 'fraud_score': 0.0}
            )
    rec = audit.records[0]
    assert rec.regulatory_flags['adverse_action_eligible'] is True


@pytest.mark.asyncio
async def test_metrics_recorded() -> None:
    app = _make_app(registry=FakeRegistry(_HIGH_UPLIFT))
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-001', 'fraud_score': 0.0}
            )
    assert resp.status_code == 200
    collector: MetricsCollector = app.state.metrics
    assert len(collector._buf) == 1


# ── Tests: rate limiting ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_per_customer() -> None:
    app = _make_app(
        registry=FakeRegistry(_HIGH_UPLIFT),
        per_customer_rps=2,
        global_rps=1000,
    )
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            statuses = []
            for _ in range(5):
                resp = await c.post(
                    '/decide',
                    json={'customer_id': 'rate-cust', 'fraud_score': 0.0},
                )
                statuses.append(resp.status_code)
    assert statuses[0] == 200
    assert 429 in statuses


@pytest.mark.asyncio
async def test_rate_limit_global() -> None:
    app = _make_app(
        registry=FakeRegistry(_HIGH_UPLIFT),
        per_customer_rps=100,
        global_rps=2,
    )
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            statuses = []
            for i in range(5):
                resp = await c.post(
                    '/decide',
                    json={'customer_id': f'global-{i}', 'fraud_score': 0.0},
                )
                statuses.append(resp.status_code)
    assert statuses[0] == 200
    assert 429 in statuses


@pytest.mark.asyncio
async def test_rate_limit_different_customers_independent() -> None:
    app = _make_app(
        registry=FakeRegistry(_HIGH_UPLIFT),
        per_customer_rps=2,
        global_rps=1000,
    )
    p_fetch, p_cols = _patches()
    with p_fetch, p_cols:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url='http://test'
        ) as c:
            for cust in ('alice', 'bob'):
                resp = await c.post(
                    '/decide',
                    json={'customer_id': cust, 'fraud_score': 0.0},
                )
                assert resp.status_code == 200
