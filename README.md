# Kickbase Auto-Manager

Dockerisierter, KI-gestützter Auto-Manager für [Kickbase](https://www.kickbase.com/).
Details zu Anforderungen, Architektur und Roadmap in [`PROJEKT.md`](./PROJEKT.md).

> ⚠️ **Hinweis:** Nutzt eine inoffizielle Kickbase-API. Nur Privatnutzung, Account-Ban-Risiko liegt beim Betreiber.

## Quick Start (Development)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

uvicorn app.main:app --reload
# → http://127.0.0.1:8000/health
```

## Docker (Raspberry Pi, arm64)

```bash
docker build -t kickbase-auto-manager .
docker run -d --name kb \
  -p 8000:8000 \
  -v kb_data:/data \
  --restart unless-stopped \
  kickbase-auto-manager
```

## Entwicklung

| Kommando            | Zweck                                    |
|---------------------|------------------------------------------|
| `ruff check .`      | Linting                                  |
| `ruff format .`     | Formatierung                             |
| `mypy`              | Typprüfung (Domain + Application strict) |
| `pytest`            | Tests + Coverage                         |
| `pre-commit run -a` | Alle Hooks auf gesamtem Repo             |

## Projektstruktur

```
app/
├── domain/          # Business-Entities, framework-frei
├── application/     # Use-Cases
├── infrastructure/  # Adapter (Kickbase, LLM, DB, Scheduler, SMTP)
└── interface/       # FastAPI + HTMX
```

Abhängigkeiten zeigen **immer nach innen** (Interface → Application → Domain).

## Lizenz
MIT
