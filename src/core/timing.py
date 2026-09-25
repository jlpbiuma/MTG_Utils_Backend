"""HTTP request timing middleware for latency audits."""
from __future__ import annotations

import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.config import settings

logger = logging.getLogger("mtg_backend.timing")


class RequestTimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000.0
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

        path = request.url.path
        if path in ("/api/health", "/") or path.startswith("/docs") or path.startswith("/openapi"):
            return response

        threshold = settings.SLOW_REQUEST_MS
        level = logging.WARNING if duration_ms >= threshold else logging.INFO
        logger.log(
            level,
            "http_request method=%s path=%s status=%s duration_ms=%.1f",
            request.method,
            path,
            response.status_code,
            duration_ms,
        )
        return response
