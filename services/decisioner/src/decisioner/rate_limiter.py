"""In-process sliding-window rate limiter (no Redis dependency).

Two tiers:
  - Per-customer: max N requests/second per customer_id
  - Global: max M requests/second across all customers

Returns 429 Too Many Requests when either limit is exceeded.
"""

from __future__ import annotations

import time
from collections import defaultdict

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse


class _SlidingCounter:
    __slots__ = ('_window_start', '_count')

    def __init__(self) -> None:
        self._window_start: float = 0.0
        self._count: int = 0

    def check_and_increment(self, now: float, max_rps: int) -> bool:
        if now - self._window_start >= 1.0:
            self._window_start = now
            self._count = 0
        if self._count >= max_rps:
            return False
        self._count += 1
        return True


class RateLimiterMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        per_customer_rps: int = 10,
        global_rps: int = 1000,
    ) -> None:
        super().__init__(app)
        self._per_customer_rps = per_customer_rps
        self._global_rps = global_rps
        self._global = _SlidingCounter()
        self._customers: dict[str, _SlidingCounter] = defaultdict(_SlidingCounter)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path != '/decide':
            return await call_next(request)

        now = time.monotonic()

        if not self._global.check_and_increment(now, self._global_rps):
            return JSONResponse(
                status_code=429,
                content={'detail': 'global rate limit exceeded'},
            )

        try:
            body = await request.json()
            customer_id = body.get('customer_id', '')
        except Exception:
            return await call_next(request)

        if customer_id:
            counter = self._customers[customer_id]
            if not counter.check_and_increment(now, self._per_customer_rps):
                return JSONResponse(
                    status_code=429,
                    content={'detail': 'per-customer rate limit exceeded'},
                )

        return await call_next(request)
