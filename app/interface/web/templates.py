"""Jinja2-Environment für die Web-Schicht — zentral, damit Auto-Reload konsistent bleibt."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
