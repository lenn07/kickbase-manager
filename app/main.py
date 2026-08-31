"""Composition Root — verdrahtet Interface-, Application- und Infrastructure-Layer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app import __version__
from app.application.decision_engine import DecisionEngine
from app.application.digest_scheduling import apply_digest_settings
from app.application.heuristic_engine import HeuristicDecisionEngine
from app.application.llm_curator import LlmCurator
from app.application.run_tick_uc import RunTickUseCase, TickOutcome
from app.config import Settings, get_settings
from app.infrastructure.crypto.vault import CryptoError, FernetVault
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.llm.anthropic_client import AnthropicClient
from app.infrastructure.logging import (
    BroadcastLogHandler,
    LogBroadcaster,
    install_redaction_filter,
)
from app.infrastructure.metrics import get_metrics
from app.infrastructure.metrics.middleware import PrometheusMiddleware
from app.infrastructure.notifications.smtp_client import AiosmtplibClient
from app.infrastructure.persistence.db import init_db, make_engine
from app.infrastructure.persistence.repositories import (
    CredentialRepository,
    SettingsRepository,
    UserRepository,
)
from app.infrastructure.persistence.session_store import DbSessionStore
from app.infrastructure.scheduler import KickbaseScheduler
from app.interface.api import router as api_router
from app.interface.api.logs import router as logs_router
from app.interface.api.scheduler import router as scheduler_router
from app.interface.web.dashboard import router as dashboard_router
from app.interface.web.router import router as web_router
from app.interface.web.settings import router as settings_router

_log = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.db_path)
    init_db(engine)
    vault = FernetVault.load_or_create(data_dir=settings.data_dir, secret_file=settings.secret_file)

    metrics = get_metrics()
    scheduler = _build_scheduler(engine, vault, settings) if settings.scheduler_enabled else None
    log_broadcaster = LogBroadcaster()
    log_handler = _install_log_handler(log_broadcaster, level_name=settings.log_level)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        log_broadcaster.bind_loop(asyncio.get_running_loop())
        if scheduler is not None:
            scheduler.start()
            metrics.scheduler_running.set(1)
            metrics.scheduler_paused.set(0)
            apply_digest_settings(scheduler, engine, vault)
        else:
            metrics.scheduler_running.set(0)
        try:
            yield
        finally:
            if scheduler is not None:
                await scheduler.shutdown()
            metrics.scheduler_running.set(0)
            logging.getLogger().removeHandler(log_handler)
            engine.dispose()

    app = FastAPI(
        title="Kickbase Auto-Manager",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(PrometheusMiddleware, metrics=metrics)
    app.state.settings = settings
    app.state.engine = engine
    app.state.vault = vault
    app.state.scheduler = scheduler
    app.state.log_broadcaster = log_broadcaster
    app.state.metrics = metrics

    app.include_router(api_router)
    app.include_router(scheduler_router)
    app.include_router(logs_router)
    app.include_router(dashboard_router)
    app.include_router(settings_router)
    app.include_router(web_router)
    return app


def _install_log_handler(
    broadcaster: LogBroadcaster, *, level_name: str = "INFO"
) -> BroadcastLogHandler:
    handler = BroadcastLogHandler(broadcaster)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root = logging.getLogger()
    # Redaction MUSS vor Handler-Emission greifen, damit Broadcast + stdout die
    # maskierte Fassung sehen. Am Root-Logger installiert wirkt der Filter für
    # alle Kind-Logger, die per Default an den Root propagieren.
    install_redaction_filter(root)
    root.addHandler(handler)
    level = logging.getLevelNamesMapping().get(level_name.upper(), logging.INFO)
    root.setLevel(level)
    return handler


def _build_scheduler(engine: Engine, vault: FernetVault, settings: Settings) -> KickbaseScheduler:
    async def tick() -> TickOutcome:
        return await _run_tick(engine, vault)

    interval = _resolve_initial_interval(engine, settings)
    return KickbaseScheduler(tick=tick, interval_min=interval, timezone=settings.timezone)


def _resolve_initial_interval(engine: Engine, settings: Settings) -> int:
    """Nimmt das persistierte Intervall des Nutzers — sonst den ENV-Default.

    Vor Setup existiert kein User; wir fallen auf `settings.default_interval_min`
    zurück und rescheduln nach abgeschlossenem Setup manuell (Phase 6).
    """
    with Session(engine) as db:
        user = UserRepository(db).get_singleton()
        if user is None or user.id is None:
            return settings.default_interval_min
        row = SettingsRepository(db).get(user.id)
        return row.interval_min if row is not None else settings.default_interval_min


async def _run_tick(engine: Engine, vault: FernetVault) -> TickOutcome:
    """Ein Tick = frische DB-Session + frischer Kickbase-Client + LLM-Kurator.

    Der Kickbase-Client hält keinen Cross-Tick-State, damit ein 401 im nächsten
    Tick sauber via Relogin repariert werden kann. Der LLM-Kurator (Phase 5)
    legt Claude Sonnet über die Heuristik; fehlt der Anthropic-Key (Setup
    unvollständig oder Row korrupt), wird auf die reine Heuristik zurückgefallen
    — der Setup-Check im UseCase skippt den Tick dann sowieso.
    """
    smtp = AiosmtplibClient()
    anthropic = AnthropicClient()
    with Session(engine) as db:
        store = DbSessionStore(db, vault)
        kickbase = HttpxKickbaseClient(session_store=store)
        try:
            heuristic = HeuristicDecisionEngine(kickbase)
            decision_engine = _build_decision_engine(db, vault, heuristic, anthropic)
            uc = RunTickUseCase(
                session=db,
                vault=vault,
                kickbase=kickbase,
                engine=decision_engine,
                smtp=smtp,
            )
            return await uc.run()
        finally:
            await kickbase.aclose()


def _build_decision_engine(
    db: Session,
    vault: FernetVault,
    heuristic: HeuristicDecisionEngine,
    anthropic: AnthropicClient,
) -> DecisionEngine:
    """Setzt LlmCurator obendrauf, wenn ein entschlüsselbarer Anthropic-Key vorliegt."""
    user = UserRepository(db).get_singleton()
    if user is None or user.id is None:
        return heuristic
    credential = CredentialRepository(db).get(user_id=user.id, kind="anthropic")
    if credential is None:
        return heuristic
    try:
        api_key = vault.decrypt(credential.encrypted_value)
    except CryptoError:
        _log.warning("Anthropic-Key konnte nicht entschlüsselt werden — nur Heuristik aktiv.")
        return heuristic
    return LlmCurator(heuristic=heuristic, llm=anthropic, api_key=api_key)
