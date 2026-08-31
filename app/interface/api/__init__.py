"""JSON-API-Endpunkte (Health, Metrics, WebSocket)."""

from fastapi import APIRouter
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.infrastructure.metrics import get_metrics

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics", tags=["ops"], include_in_schema=False)
async def metrics() -> Response:
    payload = generate_latest(get_metrics().registry)
    return Response(content=payload, media_type=CONTENT_TYPE_LATEST)
