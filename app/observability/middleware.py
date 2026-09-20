"""Request-scoped observability: trace ids, latency and status metrics."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.observability.metrics import (
    http_exceptions_total,
    http_request_duration_seconds,
    http_requests_total,
)

logger = logging.getLogger(__name__)

TRACE_HEADER = "X-Trace-Id"


def route_label(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return str(path)
    return "unmatched"


def new_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:16]}"


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        trace_id = request.headers.get(TRACE_HEADER) or new_trace_id()
        request.state.trace_id = trace_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            http_exceptions_total.labels(route=route_label(request)).inc()
            http_requests_total.labels(
                method=request.method, route=route_label(request), status="500"
            ).inc()
            http_request_duration_seconds.labels(
                method=request.method, route=route_label(request)
            ).observe(time.perf_counter() - started)
            logger.exception("Unhandled error on %s %s (trace %s)", request.method, request.url.path, trace_id)
            raise

        elapsed = time.perf_counter() - started
        label = route_label(request)
        http_requests_total.labels(
            method=request.method, route=label, status=str(response.status_code)
        ).inc()
        http_request_duration_seconds.labels(method=request.method, route=label).observe(elapsed)
        response.headers[TRACE_HEADER] = trace_id
        response.headers["X-Synthetic-Data"] = "DemoBank-educational-demo"
        return response
