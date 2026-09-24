"""Composition Root — verdrahtet Interface-, Application- und Infrastructure-Layer."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app import __version__
from app.application.ai_decision_engine import AiDecisionEngine
from app.application.decision_engine import DecisionEngine, HoldOnlyDecisionEngine
from app.application.digest_scheduling import apply_digest_settings
from app.application.player_enrichment import PlayerEnricher
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
    MarketMetaRepository,
    MarketValueCacheRepository,
    PlayerPerformanceCacheRepository,
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
            _apply_initial_windows(scheduler, engine)
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
    scheduler: KickbaseScheduler

    async def tick() -> TickOutcome:
        outcome = await _run_tick(engine, vault, settings)
        # Der Anpfiff wandert, sobald ein Spieltag durch ist — die beweglichen
        # Fenster werden deshalb nach jedem Tick neu gelegt (P1-10).
        if outcome.next_matchday_start is not None:
            scheduler.set_windows(outcome.next_matchday_start, now=datetime.now(UTC))
        return outcome

    interval = _resolve_initial_interval(engine, settings)
    scheduler = KickbaseScheduler(tick=tick, interval_min=interval, timezone=settings.timezone)
    return scheduler


def _apply_initial_windows(scheduler: KickbaseScheduler, engine: Engine) -> None:
    """Setzt die Fenster beim Start aus der letzten bekannten Lage.

    Ohne diesen Schritt hätte der Container nach einem Neustart bis zum ersten
    Intervall-Tick kein Deadline-Fenster — bei 120 Minuten Takt also
    möglicherweise über den Anpfiff hinweg. Die festen Uhrzeiten stehen
    ohnehin, den Anpfiff liefert die letzte gespeicherte Zeile.
    """
    with Session(engine) as db:
        row = MarketMetaRepository(db).latest()
    next_start = row.next_matchday_start if row is not None else None
    if next_start is not None and next_start.tzinfo is None:
        next_start = next_start.replace(tzinfo=UTC)
    scheduler.set_windows(next_start, now=datetime.now(UTC))


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


async def _run_tick(engine: Engine, vault: FernetVault, settings: Settings) -> TickOutcome:
    """Ein Tick = frische DB-Session + frischer Kickbase-Client + AI-Only-Engine.

    Der Kickbase-Client hält keinen Cross-Tick-State, damit ein 401 im nächsten
    Tick sauber via Relogin repariert werden kann. Der Master-Prompt-Modus
    verlangt einen entschlüsselbaren Anthropic-Key — ohne Key fällt der Tick
    auf `HoldOnlyDecisionEngine` zurück, der Setup-Check im UseCase blockt
    den Auto-Loop ohnehin, bis der Nutzer das Setup abschließt.
    """
    smtp = AiosmtplibClient()
    anthropic = AnthropicClient()
    with Session(engine) as db:
        store = DbSessionStore(db, vault)
        kickbase = HttpxKickbaseClient(session_store=store)
        try:
            decision_engine = _build_decision_engine(db, vault, anthropic)
            # Der Cache lebt in derselben DB-Session wie der Tick und wird mit
            # ihr geschlossen — er ist ein Tages-Cache, kein Prozess-State.
            enricher = PlayerEnricher(
                kickbase,
                cache=MarketValueCacheRepository(db),
                performance_cache=PlayerPerformanceCacheRepository(db),
            )
            uc = RunTickUseCase(
                session=db,
                vault=vault,
                kickbase=kickbase,
                engine=decision_engine,
                smtp=smtp,
                enricher=enricher,
                lineup_writes_enabled=settings.lineup_writes_enabled,
                club_limit=settings.club_limit_value,
                club_limit_is_unlimited=settings.club_limit_is_unlimited,
                underpay_blocked=settings.underpay_blocked,
                scoring_mode=settings.scoring_mode,
            )
            return await uc.run()
        finally:
            await kickbase.aclose()


def _build_decision_engine(
    db: Session,
    vault: FernetVault,
    anthropic: AnthropicClient,
) -> DecisionEngine:
    """Baut die AI-Only-Engine, wenn ein entschlüsselbarer Anthropic-Key vorliegt.

    Fällt auf `HoldOnlyDecisionEngine` zurück, wenn (a) noch kein User existiert
    (Pre-Setup-Phase), (b) kein Anthropic-Credential hinterlegt ist oder (c) der
    Vault den Key nicht mehr entschlüsseln kann. In allen drei Fällen greift
    ohnehin der Setup-Check im RunTickUseCase, sodass keine echte Aktion läuft.
    """
    user = UserRepository(db).get_singleton()
    if user is None or user.id is None:
        return HoldOnlyDecisionEngine()
    credential = CredentialRepository(db).get(user_id=user.id, kind="anthropic")
    if credential is None:
        return HoldOnlyDecisionEngine()
    try:
        api_key = vault.decrypt(credential.encrypted_value)
    except CryptoError:
        _log.warning(
            "Anthropic-Key konnte nicht entschlüsselt werden — Auto-Loop pausiert auf HOLD."
        )
        return HoldOnlyDecisionEngine()
    return AiDecisionEngine(llm=anthropic, api_key=api_key)
