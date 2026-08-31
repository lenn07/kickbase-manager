"""End-to-End-Test des WebSocket-Log-Streams."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    settings = Settings(data_dir=tmp_path, scheduler_enabled=False)
    with TestClient(create_app(settings)) as tc:
        yield tc


def test_ws_streams_history_and_live_records(client: TestClient) -> None:
    # Vor Verbindung einen Record ins History-Puffer schreiben.
    logging.getLogger("app.test").info("historical")

    with client.websocket_connect("/ws/logs") as ws:
        # History-Frames zuerst — mindestens der obige Record.
        first = ws.receive_json()
        assert first["message"] == "historical"

        # Anschließend live: nach Connect gefeuertes Log muss ankommen.
        logging.getLogger("app.test").warning("live-warn")
        payload = ws.receive_json()
        # Falls zwischen den Punkten weitere Records kommen, iterieren.
        while payload.get("message") != "live-warn":
            payload = ws.receive_json()
        assert payload["level"] == "WARNING"
        assert payload["logger"] == "app.test"
