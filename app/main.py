"""Composition Root — verdrahtet Interface-, Application- und Infrastructure-Layer."""

from __future__ import annotations

from fastapi import FastAPI

from app import __version__
from app.interface.api import router as api_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kickbase Auto-Manager",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.include_router(api_router)
    return app


app = create_app()
