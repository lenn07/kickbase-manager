"""Composition Root — verdrahtet Interface-, Application- und Infrastructure-Layer."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.config import Settings, get_settings
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.persistence.db import init_db, make_engine
from app.interface.api import router as api_router
from app.interface.web.router import router as web_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    engine = make_engine(settings.db_path)
    init_db(engine)
    vault = FernetVault.load_or_create(data_dir=settings.data_dir, secret_file=settings.secret_file)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(
        title="Kickbase Auto-Manager",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.vault = vault

    app.include_router(api_router)
    app.include_router(web_router)
    return app
