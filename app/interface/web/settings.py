"""Settings-Editor (§ 2.1 F-3 Intervall, F-10 Guardrails).

Zeigt und aktualisiert die Runtime-Overrides in `SettingsRow`. Bei Änderung
des Intervalls wird der laufende APScheduler über `reschedule()` sofort
neu getriggert — kein App-Restart nötig.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.application.digest_scheduling import apply_digest_settings
from app.application.setup_state import read_setup_state
from app.infrastructure.persistence.models import SettingsRow
from app.infrastructure.persistence.repositories import (
    SettingsRepository,
    UserRepository,
)
from app.infrastructure.scheduler import KickbaseScheduler
from app.interface.api.scheduler import get_scheduler
from app.interface.web.dependencies import SessionDep
from app.interface.web.templates import templates

router = APIRouter(tags=["settings"])
_log = logging.getLogger(__name__)

# Range aus PROJEKT.md F-3.
_INTERVAL_MIN = 15
_INTERVAL_MAX = 24 * 60
_DIGEST_HOUR_MIN = 0
_DIGEST_HOUR_MAX = 23
# Obergrenze der Heuristik-Utility (siehe HeuristicDecisionEngine); Schwellwert
# darüber wäre praktisch nie erreichbar und ergibt keine sinnvolle Semantik.
_ACTION_SCORE_MAX = 10.0


@dataclass(frozen=True, slots=True)
class SettingsFormData:
    """Roher Formular-Zustand für Re-Render nach Validierungsfehler."""

    interval_min: int
    dry_run: bool
    max_trade_pct: float
    min_cash_reserve: int
    min_action_score: float
    blacklist_text: str
    digest_enabled: bool
    digest_hour: int

    @classmethod
    def from_row(cls, row: SettingsRow) -> SettingsFormData:
        return cls(
            interval_min=row.interval_min,
            dry_run=row.dry_run,
            max_trade_pct=row.max_trade_pct,
            min_cash_reserve=row.min_cash_reserve,
            min_action_score=row.min_action_score,
            blacklist_text="\n".join(row.blacklist),
            digest_enabled=row.digest_enabled,
            digest_hour=row.digest_hour,
        )


@router.get("/settings", response_class=HTMLResponse)
async def settings_form(request: Request, session: SessionDep) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return RedirectResponse(url="/setup", status_code=303)

    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None
    row = SettingsRepository(session).get_or_default(user.id)
    return _render(request, SettingsFormData.from_row(row))


@router.post("/settings", response_class=HTMLResponse)
async def settings_submit(
    request: Request,
    session: SessionDep,
    scheduler: Annotated[KickbaseScheduler | None, Depends(get_scheduler)],
    interval_min: Annotated[int, Form()],
    max_trade_pct: Annotated[float, Form()],
    min_cash_reserve: Annotated[int, Form()],
    min_action_score: Annotated[float, Form()],
    digest_hour: Annotated[int, Form()],
    blacklist: Annotated[str, Form()] = "",
    dry_run: Annotated[str, Form()] = "",
    digest_enabled: Annotated[str, Form()] = "",
) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return RedirectResponse(url="/setup", status_code=303)

    form = SettingsFormData(
        interval_min=interval_min,
        dry_run=_checkbox(dry_run),
        max_trade_pct=max_trade_pct,
        min_cash_reserve=min_cash_reserve,
        min_action_score=min_action_score,
        blacklist_text=blacklist,
        digest_enabled=_checkbox(digest_enabled),
        digest_hour=digest_hour,
    )

    error = _validate(form)
    if error is not None:
        return _render(request, form, error=error)

    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None
    repo = SettingsRepository(session)
    old = repo.get_or_default(user.id)
    old_interval = old.interval_min

    updated = SettingsRow(
        user_id=user.id,
        interval_min=form.interval_min,
        dry_run=form.dry_run,
        max_trade_pct=form.max_trade_pct,
        min_cash_reserve=form.min_cash_reserve,
        min_action_score=form.min_action_score,
        blacklist=_parse_blacklist(form.blacklist_text),
        digest_enabled=form.digest_enabled,
        digest_hour=form.digest_hour,
    )
    repo.upsert(updated)

    reschedule_note: str | None = None
    if scheduler is not None and updated.interval_min != old_interval:
        try:
            scheduler.reschedule(updated.interval_min)
            reschedule_note = (
                f"Intervall aktualisiert: {old_interval} min → {updated.interval_min} min."
            )
        except ValueError as exc:
            _log.warning("Reschedule fehlgeschlagen: %s", exc)
            reschedule_note = f"Intervall gespeichert, Reschedule fehlgeschlagen: {exc}"

    digest_changed = (
        updated.digest_enabled != old.digest_enabled or updated.digest_hour != old.digest_hour
    )
    if scheduler is not None and digest_changed:
        apply_digest_settings(scheduler, request.app.state.engine, request.app.state.vault)

    return _render(
        request,
        SettingsFormData.from_row(updated),
        success="Einstellungen gespeichert.",
        info=reschedule_note,
    )


def _render(
    request: Request,
    form: SettingsFormData,
    *,
    error: str | None = None,
    success: str | None = None,
    info: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "form": form,
            "error": error,
            "success": success,
            "info": info,
            "interval_min_bound": _INTERVAL_MIN,
            "interval_max_bound": _INTERVAL_MAX,
        },
    )


def _validate(form: SettingsFormData) -> str | None:
    if not _INTERVAL_MIN <= form.interval_min <= _INTERVAL_MAX:
        return f"Intervall muss zwischen {_INTERVAL_MIN} und {_INTERVAL_MAX} min liegen."
    if not 0.0 < form.max_trade_pct <= 1.0:
        return "max_trade_pct muss zwischen 0.01 und 1.0 liegen."
    if form.min_cash_reserve < 0:
        return "min_cash_reserve darf nicht negativ sein."
    if not 0.0 <= form.min_action_score <= _ACTION_SCORE_MAX:
        return "min_action_score muss zwischen 0 und 10 liegen."
    if not _DIGEST_HOUR_MIN <= form.digest_hour <= _DIGEST_HOUR_MAX:
        return "digest_hour muss zwischen 0 und 23 liegen."
    return None


def _parse_blacklist(text: str) -> list[str]:
    """Ein Player-Identifier pro Zeile; leere Zeilen und Duplikate entfernen."""
    seen: list[str] = []
    for raw in text.splitlines():
        value = raw.strip()
        if value and value not in seen:
            seen.append(value)
    return seen


def _checkbox(value: str) -> bool:
    """HTML-Checkboxes senden bei off gar nichts, bei on den Value 'on' o. ä."""
    return value.strip().lower() in {"on", "true", "1", "yes"}
