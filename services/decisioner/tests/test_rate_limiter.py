"""Unit tests for the in-process sliding-window rate limiter."""

from __future__ import annotations

import pytest
from decisioner.rate_limiter import RateLimiterMiddleware, _SlidingCounter
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import JSONResponse

# ── _SlidingCounter unit tests ────────────────────────────────────


def test_counter_allows_within_limit() -> None:
    c = _SlidingCounter()
    assert c.check_and_increment(100.0, 3) is True
    assert c.check_and_increment(100.0, 3) is True
    assert c.check_and_increment(100.0, 3) is True


def test_counter_blocks_over_limit() -> None:
    c = _SlidingCounter()
    for _ in range(5):
        c.check_and_increment(100.0, 5)
    assert c.check_and_increment(100.0, 5) is False


def test_counter_resets_after_window() -> None:
    c = _SlidingCounter()
    for _ in range(5):
        c.check_and_increment(100.0, 5)
    assert c.check_and_increment(100.0, 5) is False
    assert c.check_and_increment(101.1, 5) is True


def test_counter_exactly_at_limit() -> None:
    c = _SlidingCounter()
    results = [c.check_and_increment(0.0, 2) for _ in range(3)]
    assert results == [True, True, False]


# ── Middleware integration tests ──────────────────────────────────


def _make_app(per_customer_rps: int = 10, global_rps: int = 1000) -> FastAPI:
    app = FastAPI()

    @app.post('/decide')
    async def decide():
        return JSONResponse({'action': 'OFFER_CLI'})

    @app.get('/health')
    async def health():
        return {'status': 'ok'}

    app.add_middleware(
        RateLimiterMiddleware,
        per_customer_rps=per_customer_rps,
        global_rps=global_rps,
    )
    return app


@pytest.mark.asyncio
async def test_non_decide_routes_bypass_limiter() -> None:
    app = _make_app(per_customer_rps=1, global_rps=1)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        for _ in range(5):
            resp = await c.get('/health')
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_per_customer_limit_enforced() -> None:
    app = _make_app(per_customer_rps=2, global_rps=1000)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        statuses = []
        for _ in range(4):
            resp = await c.post(
                '/decide', json={'customer_id': 'cust-a', 'fraud_score': 0.0}
            )
            statuses.append(resp.status_code)
    assert statuses[:2] == [200, 200]
    assert all(s == 429 for s in statuses[2:])


@pytest.mark.asyncio
async def test_per_customer_limit_independent() -> None:
    app = _make_app(per_customer_rps=1, global_rps=1000)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        r1 = await c.post('/decide', json={'customer_id': 'alice', 'fraud_score': 0.0})
        r2 = await c.post('/decide', json={'customer_id': 'bob', 'fraud_score': 0.0})
    assert r1.status_code == 200
    assert r2.status_code == 200


@pytest.mark.asyncio
async def test_global_limit_enforced() -> None:
    app = _make_app(per_customer_rps=100, global_rps=2)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        statuses = []
        for i in range(4):
            resp = await c.post(
                '/decide', json={'customer_id': f'cust-{i}', 'fraud_score': 0.0}
            )
            statuses.append(resp.status_code)
    assert statuses[:2] == [200, 200]
    assert 429 in statuses[2:]


@pytest.mark.asyncio
async def test_global_checked_before_per_customer() -> None:
    app = _make_app(per_customer_rps=100, global_rps=1)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        r1 = await c.post('/decide', json={'customer_id': 'cust-x', 'fraud_score': 0.0})
        r2 = await c.post('/decide', json={'customer_id': 'cust-x', 'fraud_score': 0.0})
    assert r1.status_code == 200
    assert r2.status_code == 429
    assert 'global' in r2.json()['detail']


@pytest.mark.asyncio
async def test_429_body_per_customer() -> None:
    app = _make_app(per_customer_rps=1, global_rps=1000)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        await c.post('/decide', json={'customer_id': 'cust-z', 'fraud_score': 0.0})
        resp = await c.post(
            '/decide', json={'customer_id': 'cust-z', 'fraud_score': 0.0}
        )
    assert resp.status_code == 429
    assert 'per-customer' in resp.json()['detail']


@pytest.mark.asyncio
async def test_malformed_body_passes_through() -> None:
    app = _make_app(per_customer_rps=1, global_rps=1000)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as c:
        resp = await c.post('/decide', content=b'not json')
    assert resp.status_code in (200, 422)
