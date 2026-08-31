"""ASGI-Middleware, die HTTP-Requests in die Metrik-Registry zählt.

Bewusst als reine ASGI-Middleware (nicht `BaseHTTPMiddleware`) implementiert:
`BaseHTTPMiddleware` teilt das Scope nicht mit dem inneren Endpoint, sodass
die geroutete Route (in `scope["route"]`) unsichtbar bleibt. Bei einer echten
ASGI-Middleware sehen wir die Route-Auflösung, sobald der innere Handler
zurückkehrt.

`/metrics` und `/health` werden nicht instrumentiert (Self-Scrape-Noise;
Health-Checks würden die Requests-Rate dominieren).
"""

from __future__ import annotations

import time

from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.infrastructure.metrics.registry import MetricsRegistry

_SKIP_PATHS = frozenset({"/metrics", "/health"})


class PrometheusMiddleware:
    def __init__(self, app: ASGIApp, metrics: MetricsRegistry) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _SKIP_PATHS:
            await self._app(scope, receive, send)
            return

        start = time.perf_counter()
        status_code: list[int] = [500]

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_code[0] = int(message["status"])
            await send(message)

        try:
            await self._app(scope, receive, send_wrapper)
        finally:
            duration = time.perf_counter() - start
            self._metrics.observe_http(
                method=scope.get("method", "GET"),
                route=_resolve_route_template(scope),
                status=status_code[0],
                duration_s=duration,
            )


def _resolve_route_template(scope: Scope) -> str:
    """Liest das Pfad-Template aus dem Scope, das Starlette beim Routing setzt."""
    route = scope.get("route")
    if isinstance(route, Route):
        return route.path
    return "<unmatched>"
