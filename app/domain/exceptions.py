"""Typisierte Domain-Exceptions — HTTP-Codes bleiben in der Infrastructure-Schicht."""

from __future__ import annotations


class KickbaseError(Exception):
    """Basis für alle Kickbase-bezogenen Fehler."""


class AuthError(KickbaseError):
    """Ungültige Credentials oder abgelaufener Token, der nicht refresht werden konnte."""


class RateLimitError(KickbaseError):
    """Zu viele Requests — Backoff nötig."""


class NotFoundError(KickbaseError):
    """Angeforderte Ressource existiert nicht."""


class ConflictError(KickbaseError):
    """Fachlicher Konflikt (z. B. Gebot zu niedrig, Spieler bereits verkauft)."""


class TransportError(KickbaseError):
    """Netzwerkfehler oder unerwartete Server-Antwort (5xx)."""
