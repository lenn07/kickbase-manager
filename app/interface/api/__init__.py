"""JSON-API-Endpunkte (Health, Metrics, WebSocket)."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    return {"status": "ok"}
