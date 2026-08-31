"""IP-basiertes Rate-Limit für Login-Endpoints (Phase 7, Brute-Force-Schutz).

Bewusst simpel: In-Memory-Sliding-Window, sekundengenau. Reicht für den
Single-Container-Betrieb — kein Bedarf für Redis. Bei Restart wird der Zähler
zurückgesetzt (Feature, nicht Bug: nach Deploy ist die Uhr sowieso frisch).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status


class LoginRateLimiter:
    """Thread-safe Sliding-Window-Limiter pro Client-IP.

    Sync gehalten, weil FastAPI-Dependencies auch synchron laufen dürfen und
    ein Lock hier billiger ist als Async-Lock-Overhead — das Fenster wird nur
    beim Formular-Submit angefasst.
    """

    def __init__(self, *, max_attempts: int, window_s: float) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts muss > 0 sein.")
        self._max = max_attempts
        self._window = window_s
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, client_ip: str) -> None:
        """Registriert einen Versuch — wirft HTTP 429, wenn das Limit voll ist."""
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            events = self._events[client_ip]
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self._max:
                retry_in = int(self._window - (now - events[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=(f"Zu viele Login-Versuche. Bitte in {retry_in}s erneut versuchen."),
                    headers={"Retry-After": str(retry_in)},
                )
            events.append(now)

    def reset(self, client_ip: str | None = None) -> None:
        """Für Tests: einzelne IP oder alle Zähler löschen."""
        with self._lock:
            if client_ip is None:
                self._events.clear()
            else:
                self._events.pop(client_ip, None)


# Globale Instanz — bewusst modul-scoped, damit sie über Requests hinweg lebt.
# Werte konservativ gewählt: 5 Versuche/min ist genug für versehentliche Tipper,
# blockiert aber effektiv automatisierte Brute-Force-Angriffe.
login_limiter = LoginRateLimiter(max_attempts=5, window_s=60.0)


def _client_ip(request: Request) -> str:
    # X-Forwarded-For hat Vorrang, falls hinter Reverse-Proxy (Traefik etc.).
    # Wir nehmen nur den ersten Wert, um Spoofing über nachgeschobene IPs zu
    # verhindern; wer keinen Proxy einsetzt, bekommt request.client.host.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    if request.client is not None:
        return request.client.host
    return "unknown"


def enforce_login_rate_limit(request: Request) -> None:
    """FastAPI-Dependency für Login-Endpoints."""
    login_limiter.check(_client_ip(request))
