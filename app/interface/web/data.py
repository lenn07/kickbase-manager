"""Datenverwaltung (`/data`) — Bestand ansehen, abgleichen, löschen, exportieren.

Warum es diese Seite gibt: wer in der Kickbase-App selbst handelt, hinterlässt
im Bot ein Weltbild, das nicht mehr stimmt — Details in
`app.application.data_maintenance`. Diese Seite ist der Hebel dafür, und
zugleich die einzige Stelle, an der sichtbar wird, was der Container über die
Zeit überhaupt angesammelt hat.

Alle vier Aktionen antworten mit demselben HTMX-Fragment: dem frisch gelesenen
Bestand plus einer Meldung, was gerade passiert ist. Nach jedem Klick steht
damit der neue Zustand auf dem Schirm, nicht der alte mit einem Hinweis daneben.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.application import data_maintenance as dm
from app.application.setup_state import read_setup_state
from app.infrastructure.persistence.repositories import LeagueRepository, UserRepository
from app.interface.web.dependencies import RuntimeKickbaseDep, SessionDep
from app.interface.web.templates import templates

router = APIRouter(tags=["data"])
_log = logging.getLogger(__name__)

ResultTone = Literal["ok", "warn", "error"]


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Was die Seite nach einem Klick meldet."""

    tone: ResultTone
    headline: str
    lines: tuple[str, ...] = ()


# -- Seite + Fragment --------------------------------------------------


@router.get("/data", response_class=HTMLResponse)
async def data_page(request: Request, session: SessionDep) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return RedirectResponse(url="/setup", status_code=303)
    return templates.TemplateResponse(
        request,
        "data.html",
        {"inventory": dm.collect_inventory(session), "result": None, "nav_active": "data"},
    )


def _panel(request: Request, session: SessionDep, result: ActionResult | None) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_data_panel.html",
        {"inventory": dm.collect_inventory(session), "result": result},
    )


# -- Aktionen ----------------------------------------------------------


@router.post("/data/refresh", response_class=HTMLResponse)
async def refresh_caches(request: Request, session: SessionDep) -> Response:
    """Verwirft die zwischengespeicherten Kickbase-Antworten."""
    cleared = dm.clear_caches(session)
    if cleared.total == 0:
        return _panel(
            request,
            session,
            ActionResult("ok", "Nichts zu verwerfen — die Zwischenspeicher waren schon leer."),
        )
    return _panel(
        request,
        session,
        ActionResult(
            "ok",
            f"{cleared.total} zwischengespeicherte Einträge verworfen.",
            (
                f"{cleared.market_values} Marktwert-Historien, "
                f"{cleared.performance} Leistungsdaten, "
                f"{cleared.competition} Tabelle/Spielplan.",
                "Der nächste Durchlauf holt alles frisch von Kickbase.",
            ),
        ),
    )


@router.post("/data/reconcile", response_class=HTMLResponse)
async def reconcile_with_kickbase(
    request: Request, session: SessionDep, kickbase: RuntimeKickbaseDep
) -> Response:
    """Der Knopf für „ich habe selbst gehandelt": Gedächtnis gegen die Realität."""
    user = UserRepository(session).get_singleton()
    if user is None or user.id is None:
        return _panel(request, session, ActionResult("error", "Kein eingerichtetes Konto."))
    league = LeagueRepository(session).active(user.id)
    if league is None:
        return _panel(request, session, ActionResult("error", "Keine aktive Liga ausgewählt."))

    report = await dm.reconcile(
        session,
        kickbase,
        user_id=user.id,
        manager_id=user.kb_user_id,
        league_id=league.kb_league_id,
    )
    if not report.ok:
        return _panel(
            request,
            session,
            ActionResult(
                "error",
                "Abgleich nicht möglich — Kickbase antwortet nicht.",
                (report.error or "",),
            ),
        )

    lines = [
        f"Gesehen: {report.squad_size} Spieler im Kader, {report.market_size} Angebote im "
        f"Markt, davon {report.own_listings} eigene.",
        *report.notes,
    ]
    if report.caches_cleared is not None and report.caches_cleared.total:
        lines.append(
            f"Zusätzlich {report.caches_cleared.total} zwischengespeicherte Einträge verworfen."
        )
    tone: ResultTone = "warn" if report.superseded_total else "ok"
    headline = (
        f"{report.superseded_total} Einträge als überholt markiert."
        if report.superseded_total
        else "Abgeglichen — der Bot ist auf dem aktuellen Stand."
    )
    return _panel(request, session, ActionResult(tone, headline, tuple(lines)))


@router.post("/data/forget", response_class=HTMLResponse)
async def forget_memory(request: Request, session: SessionDep) -> Response:
    """Setzt das gesamte Gedächtnis zurück, ohne die Historie zu löschen."""
    user = UserRepository(session).get_singleton()
    if user is None or user.id is None:
        return _panel(request, session, ActionResult("error", "Kein eingerichtetes Konto."))
    count = dm.forget_memory(session, user.id)
    if count == 0:
        return _panel(request, session, ActionResult("ok", "Es gab nichts mehr zu vergessen."))
    return _panel(
        request,
        session,
        ActionResult(
            "warn",
            f"{count} Einträge als überholt markiert — das Gedächtnis ist leer.",
            (
                "Die Historie bleibt unten vollständig sichtbar; der Bot leitet nur nichts "
                "mehr daraus ab.",
                "Laufende eigene Gebote kennt er ab jetzt nicht mehr, bis er neue abgibt.",
            ),
        ),
    )


@router.post("/data/purge", response_class=HTMLResponse)
async def purge_history(request: Request, session: SessionDep) -> Response:
    """Löscht die Historie endgültig."""
    user = UserRepository(session).get_singleton()
    if user is None or user.id is None:
        return _panel(request, session, ActionResult("error", "Kein eingerichtetes Konto."))
    count = dm.purge_history(session, user.id)
    return _panel(
        request,
        session,
        ActionResult(
            "warn",
            f"{count} Einträge gelöscht.",
            ("Die Aktions-Historie im Dashboard beginnt wieder bei null.",),
        ),
    )


# -- Export ------------------------------------------------------------


@router.get("/data/export.json")
async def export_json(session: SessionDep) -> Response:
    """Der gesamte Bestand als Datei — ohne Passwörter, Token und API-Keys."""
    payload = dm.build_export(session)
    stamp = payload["exported_at"][:10]
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="kickbase-daten-{stamp}.json"',
        },
    )
