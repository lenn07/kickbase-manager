"""Prometheus-Registry + Metrik-Definitionen für den Auto-Manager."""

from __future__ import annotations

import threading

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

_HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsRegistry:
    """Kapselt alle Kern-Metriken hinter einer typisierten Fassade.

    Alle Metrik-Namen mit Präfix `kb_` — folgt Prometheus-Konvention
    (`<namespace>_<name>_<unit>`) und lässt Scrape-Dashboards einfach filtern.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()

        self.ticks_total = Counter(
            "kb_ticks_total",
            "Anzahl der Scheduler-Ticks nach Ergebnis.",
            labelnames=("outcome",),
            registry=self.registry,
        )
        self.trades_total = Counter(
            "kb_trades_total",
            "Anzahl der tatsächlich ausgeführten Trades nach Aktion.",
            labelnames=("action",),
            registry=self.registry,
        )
        self.digests_total = Counter(
            "kb_hold_digests_total",
            "Anzahl versendeter HOLD-Digest-Runs nach Ergebnis.",
            labelnames=("outcome",),
            registry=self.registry,
        )
        self.last_tick_ts = Gauge(
            "kb_last_tick_timestamp_seconds",
            "Unix-Timestamp des letzten abgeschlossenen Ticks.",
            registry=self.registry,
        )
        self.next_tick_ts = Gauge(
            "kb_next_tick_timestamp_seconds",
            "Unix-Timestamp des geplanten nächsten Ticks.",
            registry=self.registry,
        )
        self.scheduler_running = Gauge(
            "kb_scheduler_running",
            "1 wenn der Scheduler läuft (auch pausiert), 0 sonst.",
            registry=self.registry,
        )
        self.scheduler_paused = Gauge(
            "kb_scheduler_paused",
            "1 wenn der Scheduler-Job pausiert ist, 0 sonst.",
            registry=self.registry,
        )
        self.setup_complete = Gauge(
            "kb_setup_complete",
            "1 wenn das Setup vollständig ist, 0 sonst.",
            registry=self.registry,
        )
        self.http_requests_total = Counter(
            "kb_http_requests_total",
            "Anzahl HTTP-Requests nach Route/Methode/Status.",
            labelnames=("method", "route", "status"),
            registry=self.registry,
        )
        self.http_request_duration = Histogram(
            "kb_http_request_duration_seconds",
            "HTTP-Request-Dauer in Sekunden.",
            labelnames=("method", "route"),
            registry=self.registry,
            buckets=_HTTP_BUCKETS,
        )

    def record_tick(self, outcome: str) -> None:
        self.ticks_total.labels(outcome=outcome).inc()

    def record_trade(self, action: str) -> None:
        self.trades_total.labels(action=action).inc()

    def record_digest(self, outcome: str) -> None:
        self.digests_total.labels(outcome=outcome).inc()

    def observe_http(self, method: str, route: str, status: int, duration_s: float) -> None:
        self.http_requests_total.labels(method=method, route=route, status=str(status)).inc()
        self.http_request_duration.labels(method=method, route=route).observe(duration_s)


class _Holder:
    """Prozess-weiter Singleton-Container für die Registry."""

    lock = threading.Lock()
    instance: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    """Prozess-weite Registry (Lazy Init, Thread-safe).

    Ein Prometheus-Scrape trifft immer dieselbe Registry, egal welche FastAPI-
    Instanz gerade lebt. Die Instanz wird lazy erzeugt, damit `reset_metrics()`
    zwischen Tests einen sauberen Zustand liefert.
    """
    with _Holder.lock:
        if _Holder.instance is None:
            _Holder.instance = MetricsRegistry()
        return _Holder.instance


def reset_metrics() -> MetricsRegistry:
    """Für Tests: verwirft die Registry und legt eine frische an."""
    with _Holder.lock:
        _Holder.instance = MetricsRegistry()
        return _Holder.instance
