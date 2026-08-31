"""FastAPI-Dependencies: DB-Session, Vault, Kickbase-Client, SetupService.

Composition-Root ist `app.main`; hier werden nur die Wiring-Funktionen exportiert,
die als Depends(...) in den Routen genutzt werden.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.engine import Engine
from sqlmodel import Session

from app.application.setup_service import SetupService
from app.config import Settings
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.kickbase.client import HttpxKickbaseClient
from app.infrastructure.llm.anthropic_client import AnthropicClient
from app.infrastructure.notifications.smtp_client import AiosmtplibClient


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_vault(request: Request) -> FernetVault:
    return request.app.state.vault  # type: ignore[no-any-return]


def get_data_dir(settings: Annotated[Settings, Depends(get_settings)]) -> Path:
    return settings.data_dir


def get_session(engine: Annotated[Engine, Depends(get_engine)]) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


async def get_setup_service(
    session: Annotated[Session, Depends(get_session)],
    vault: Annotated[FernetVault, Depends(get_vault)],
) -> AsyncIterator[SetupService]:
    kickbase = HttpxKickbaseClient()
    try:
        service = SetupService(
            session=session,
            vault=vault,
            kickbase=kickbase,
            llm=AnthropicClient(),
            smtp=AiosmtplibClient(),
        )
        yield service
    finally:
        await kickbase.aclose()


SessionDep = Annotated[Session, Depends(get_session)]
SetupServiceDep = Annotated[SetupService, Depends(get_setup_service)]
