"""Konfiguration des Kickbase-Clients."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class KickbaseClientConfig:
    base_url: str = "https://api.kickbase.com"
    user_agent: str = "Kickbase/4.5.0 (iPhone; iOS 17.5.1; Scale/3.00)"
    request_timeout_s: float = 15.0
    max_requests_per_minute: int = 30
    jitter_min_s: float = 0.15
    jitter_max_s: float = 0.75
    max_relogin_attempts: int = 1
    # Wie lange vor Ablauf ein gecachter Token als "abgelaufen" behandelt wird.
    session_expiry_margin_s: int = 300
    # 5xx/Netzwerk-Retries: 0 = kein Retry. Backoff wächst exponentiell:
    # sleep = backoff_base_s * (2 ** attempt) + Jitter aus rate_limit_jitter.
    max_retries_5xx: int = 2
    backoff_base_s: float = 0.5
