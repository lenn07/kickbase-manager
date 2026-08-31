"""Setup-Wizard-Routen (§ 2.1 F-1 + F-2).

HTMX-Pattern:
- Formular-`POST` liefert bei Erfolg `HX-Redirect` auf den nächsten Schritt.
- Bei Fehlern wird nur das Formular-Fragment mit Fehlermeldung zurückgegeben.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.application.setup_service import SetupError, SmtpFormInput
from app.application.setup_state import SetupStep, read_setup_state
from app.interface.web.dependencies import SessionDep, SetupServiceDep
from app.interface.web.rate_limit import enforce_login_rate_limit
from app.interface.web.templates import templates

router = APIRouter(tags=["setup"])


_STEP_URLS: dict[SetupStep, str] = {
    SetupStep.KICKBASE: "/setup/kickbase",
    SetupStep.LEAGUE: "/setup/leagues",
    SetupStep.ANTHROPIC: "/setup/anthropic",
    SetupStep.SMTP: "/setup/smtp",
    SetupStep.DONE: "/setup/done",
}


def _url_for_step(step: SetupStep) -> str:
    return _STEP_URLS[step]


# -- Landing / Redirects -----------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: SessionDep) -> Response:
    state = read_setup_state(session)
    if not state.is_complete:
        return RedirectResponse(url=_url_for_step(state.next_step), status_code=303)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
async def setup_root(request: Request, session: SessionDep) -> Response:
    state = read_setup_state(session)
    return RedirectResponse(url=_url_for_step(state.next_step), status_code=303)


# -- Schritt 1: Kickbase-Login -----------------------------------------


@router.get("/setup/kickbase", response_class=HTMLResponse)
async def setup_kickbase_form(request: Request, service: SetupServiceDep) -> Response:
    return _render_kickbase(request, service, prefill_email=service.current_email())


@router.post("/setup/kickbase", dependencies=[Depends(enforce_login_rate_limit)])
async def setup_kickbase_submit(
    request: Request,
    service: SetupServiceDep,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Response:
    try:
        await service.verify_kickbase(email, password)
    except SetupError as exc:
        return _render_kickbase(request, service, error=str(exc), prefill_email=email)
    return _hx_redirect(request, _url_for_step(SetupStep.LEAGUE))


# -- Schritt 2: Liga-Auswahl -------------------------------------------


@router.get("/setup/leagues", response_class=HTMLResponse)
async def setup_leagues_form(request: Request, service: SetupServiceDep) -> Response:
    state = service.state()
    if not state.kickbase_ok:
        return RedirectResponse(url=_url_for_step(SetupStep.KICKBASE), status_code=303)
    return _render_leagues(request, service)


@router.post("/setup/leagues")
async def setup_leagues_submit(
    request: Request,
    service: SetupServiceDep,
    kb_league_id: Annotated[str, Form()],
) -> Response:
    try:
        service.select_league(kb_league_id)
    except SetupError as exc:
        return _render_leagues(request, service, error=str(exc))
    return _hx_redirect(request, _url_for_step(SetupStep.ANTHROPIC))


# -- Schritt 3: Anthropic-Key ------------------------------------------


@router.get("/setup/anthropic", response_class=HTMLResponse)
async def setup_anthropic_form(request: Request, service: SetupServiceDep) -> Response:
    state = service.state()
    if not state.league_ok:
        return RedirectResponse(url=_url_for_step(state.next_step), status_code=303)
    return _render_anthropic(request, service)


@router.post("/setup/anthropic", dependencies=[Depends(enforce_login_rate_limit)])
async def setup_anthropic_submit(
    request: Request,
    service: SetupServiceDep,
    api_key: Annotated[str, Form()],
) -> Response:
    try:
        await service.verify_anthropic(api_key)
    except SetupError as exc:
        return _render_anthropic(request, service, error=str(exc))
    return _hx_redirect(request, _url_for_step(SetupStep.SMTP))


# -- Schritt 4: SMTP ---------------------------------------------------


@router.get("/setup/smtp", response_class=HTMLResponse)
async def setup_smtp_form(request: Request, service: SetupServiceDep) -> Response:
    state = service.state()
    if not state.anthropic_ok:
        return RedirectResponse(url=_url_for_step(state.next_step), status_code=303)
    return _render_smtp(request, service)


@router.post("/setup/smtp", dependencies=[Depends(enforce_login_rate_limit)])
async def setup_smtp_submit(
    request: Request,
    service: SetupServiceDep,
    host: Annotated[str, Form()],
    port: Annotated[int, Form()],
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    from_addr: Annotated[str, Form(alias="from_addr")] = "",
    to_addr: Annotated[str, Form(alias="to_addr")] = "",
    security: Annotated[str, Form()] = "tls",
) -> Response:
    form = SmtpFormInput(
        host=host,
        port=port,
        username=username,
        password=password,
        from_addr=from_addr,
        to_addr=to_addr,
        use_tls=security == "tls",
        use_starttls=security == "starttls",
    )
    try:
        await service.verify_smtp(form)
    except SetupError as exc:
        return _render_smtp(request, service, error=str(exc), form=form)
    return _hx_redirect(request, _url_for_step(SetupStep.DONE))


# -- Fertig ------------------------------------------------------------


@router.get("/setup/done", response_class=HTMLResponse)
async def setup_done(request: Request, service: SetupServiceDep) -> Response:
    state = service.state()
    if not state.is_complete:
        return RedirectResponse(url=_url_for_step(state.next_step), status_code=303)
    leagues = [choice for choice in service.list_leagues() if choice.is_active]
    if not leagues:
        raise HTTPException(status_code=500, detail="Aktive Liga fehlt trotz complete-Setup")
    return templates.TemplateResponse(
        request,
        "setup_done.html",
        {"step": SetupStep.DONE, "active_league": leagues[0], "state": state},
    )


# -- Render-Helfer ------------------------------------------------------


def _render_kickbase(
    request: Request,
    service: SetupServiceDep,
    *,
    error: str | None = None,
    prefill_email: str = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_kickbase.html",
        {
            "step": SetupStep.KICKBASE,
            "state": service.state(),
            "error": error,
            "email": prefill_email,
        },
    )


def _render_leagues(
    request: Request,
    service: SetupServiceDep,
    *,
    error: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_leagues.html",
        {
            "step": SetupStep.LEAGUE,
            "state": service.state(),
            "leagues": service.list_leagues(),
            "error": error,
        },
    )


def _render_anthropic(
    request: Request,
    service: SetupServiceDep,
    *,
    error: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_anthropic.html",
        {"step": SetupStep.ANTHROPIC, "state": service.state(), "error": error},
    )


def _render_smtp(
    request: Request,
    service: SetupServiceDep,
    *,
    error: str | None = None,
    form: SmtpFormInput | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "setup_smtp.html",
        {
            "step": SetupStep.SMTP,
            "state": service.state(),
            "error": error,
            "form": form,
        },
    )


def _hx_redirect(request: Request, target: str) -> Response:
    """HTMX bekommt `HX-Redirect`-Header; klassische Clients einen 303."""
    if request.headers.get("HX-Request") == "true":
        response = Response(status_code=204)
        response.headers["HX-Redirect"] = target
        return response
    return RedirectResponse(url=target, status_code=303)
