"""Dashboard-Routen (§ 2.1 F-6).

Struktur:
- `GET /dashboard` — vollständige Seite (nur nach abgeschlossenem Setup).
- `GET /dashboard/status` — HTMX-Fragment mit Live-Status (Budget, Kader-Wert,
  letzte Aktion, nächster Tick). Wird vom Frontend im Intervall gepollt.
- `GET /dashboard/history` — HTMX-Fragment mit den jüngsten TradeLog-Einträgen.

Der WebSocket-Log-Stream läuft parallel unter `/ws/logs` und wird vom Client
direkt (nativ) konsumiert — keine HTMX-Beteiligung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from app.application.setup_state import read_setup_state
from app.domain.exceptions import KickbaseError
from app.domain.gateways import KickbaseGateway
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import (
    LeagueRepository,
    SettingsRepository,
    TradeLogRepository,
    UserRepository,
)
from app.infrastructure.scheduler import KickbaseScheduler
from app.interface.api.scheduler import get_scheduler
from app.interface.web.dependencies import RuntimeKickbaseDep, SessionDep
from app.interface.web.templates import templates

router = APIRouter(tags=["dashboard"])

_log = logging.getLogger(__name__)

_HISTORY_LIMIT = 20


@dataclass(frozen=True, slots=True)
class LiveStatus:
    """Zusammengesetzte Anzeige-Daten für das Status-Panel."""

    league_name: str
    league_id: str
    budget: Decimal | None
    team_value: Decimal | None
    squad_size: int | None
    dry_run: bool
    interval_min: int
    scheduler_running: bool
    scheduler_paused: bool
    scheduler_enabled: bool
    next_run: datetime | None
    last_action: TradeLogRow | None
    kickbase_error: str | None


# -- Routes ------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(
    request: Request,
    session: SessionDep,
    kickbase: RuntimeKickbaseDep,
    scheduler: Annotated[KickbaseScheduler | None, Depends(get_scheduler)],
) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return RedirectResponse(url="/setup", status_code=303)

    status = await _load_status(session, kickbase, scheduler)
    history = _load_history(session)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {"status": status, "history": history},
    )


@router.get("/dashboard/status", response_class=HTMLResponse)
async def dashboard_status_fragment(
    request: Request,
    session: SessionDep,
    kickbase: RuntimeKickbaseDep,
    scheduler: Annotated[KickbaseScheduler | None, Depends(get_scheduler)],
) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return HTMLResponse("<p class='hint'>Setup unvollständig.</p>", status_code=409)
    status = await _load_status(session, kickbase, scheduler)
    return templates.TemplateResponse(request, "_dashboard_status.html", {"status": status})


@router.get("/dashboard/history", response_class=HTMLResponse)
async def dashboard_history_fragment(request: Request, session: SessionDep) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return HTMLResponse("<p class='hint'>Setup unvollständig.</p>", status_code=409)
    history = _load_history(session)
    return templates.TemplateResponse(request, "_dashboard_history.html", {"history": history})


# -- Loaders -----------------------------------------------------------


async def _load_status(
    session: Session,
    kickbase: KickbaseGateway,
    scheduler: KickbaseScheduler | None,
) -> LiveStatus:
    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None  # von setup_state garantiert

    league = LeagueRepository(session).active(user.id)
    assert league is not None

    settings = SettingsRepository(session).get_or_default(user.id)
    last_action = TradeLogRepository(session).latest(user.id)

    scheduler_status = scheduler.status() if scheduler is not None else None
    running = scheduler_status.running if scheduler_status is not None else False
    paused = scheduler_status.paused if scheduler_status is not None else False
    next_run = scheduler_status.next_run if scheduler_status is not None else None
    interval_min = (
        scheduler_status.interval_min if scheduler_status is not None else settings.interval_min
    )

    budget: Decimal | None = None
    team_value: Decimal | None = None
    squad_size: int | None = None
    kickbase_error: str | None = None
    try:
        league_me = await kickbase.get_league_me(league.kb_league_id)
        squad = await kickbase.get_squad(league.kb_league_id, user.kb_user_id)
        budget = league_me.budget
        team_value = squad.team_value
        squad_size = len(squad.players)
    except KickbaseError as exc:
        _log.warning("Kickbase-Fehler beim Dashboard-Load: %s", exc)
        kickbase_error = str(exc)

    return LiveStatus(
        league_name=league.name,
        league_id=league.kb_league_id,
        budget=budget,
        team_value=team_value,
        squad_size=squad_size,
        dry_run=settings.dry_run,
        interval_min=interval_min,
        scheduler_running=running,
        scheduler_paused=paused,
        scheduler_enabled=scheduler is not None,
        next_run=next_run,
        last_action=last_action,
        kickbase_error=kickbase_error,
    )


def _load_history(session: Session) -> list[TradeLogRow]:
    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None
    return TradeLogRepository(session).list_recent(user_id=user.id, limit=_HISTORY_LIMIT)
