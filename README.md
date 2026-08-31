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

KB_DATA_DIR=./data uvicorn --factory app.main:create_app --reload
# → http://127.0.0.1:8000/  (redirect zum Setup-Wizard)
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

## Betrieb & Härtung (Phase 7)

### Endpunkte

| Pfad         | Zweck                                                       |
|--------------|-------------------------------------------------------------|
| `/`          | Redirect zum Setup-Wizard bzw. Dashboard                    |
| `/dashboard` | Live-Status, Historie, Log-Stream                           |
| `/health`    | Health-Check für Docker/Uptime-Monitore (`{"status":"ok"}`) |
| `/metrics`   | Prometheus-Text-Format, siehe unten                         |
| `/api/docs`  | OpenAPI-Swagger                                             |

### Volume & Backup

Der Container schreibt ausschließlich unter `/data`:

- `kb.db` — SQLite (Setup, Trade-Log, Settings)
- `secret.key` — Fernet-Master-Key (Mode 0600)

**Backup:** Container stoppen (SQLite-WAL kann sonst noch inkonsistent sein),
`kb_data`-Volume tarren, wieder starten. Kürzestmöglich:

```bash
docker stop kb && \
  docker run --rm -v kb_data:/data -v $(pwd):/backup alpine \
    tar czf /backup/kb-$(date +%F).tgz -C /data . && \
  docker start kb
```

### Secrets

- **Master-Key** wird beim ersten Start als `/data/secret.key` erzeugt.
  Für produktiven Betrieb bevorzugt via Docker-Secret:
  `docker run -e KB_SECRET_FILE=/run/secrets/kb_key --secret kb_key …`.
- **Verschlüsselt in DB:** Kickbase-Passwort, Kickbase-Token, Anthropic-Key,
  SMTP-Passwort. Klartext-Werte verlassen den Prozess-Speicher nur Richtung
  Kickbase-/Anthropic-/SMTP-API.
- **Log-Redaction** maskiert Bearer-Tokens, `sk-ant-*`-Keys sowie Key-Value-
  Patterns (`password=`, `api_key=`) in stdout und Dashboard-Log-Stream.
- **Rotation:** Master-Key manuell tauschen bedeutet, alle verschlüsselten
  DB-Felder neu zu setzen — der pragmatische Weg ist Setup-Wizard erneut
  durchlaufen (Kickbase-Login, Anthropic-Key, SMTP).

### Rate-Limits

- **Kickbase-API-Calls:** Token-Bucket + Jitter, konfiguriert in
  `KickbaseClientConfig` (default 30/min).
- **Setup-Formulare** (Kickbase-Login, Anthropic-Key, SMTP): 5 Versuche pro
  Minute pro IP. `X-Forwarded-For` wird für Reverse-Proxy-Deployments
  ausgewertet.

### Prometheus-Metriken

`/metrics` liefert das offizielle Text-Format. Kern-Serien:

| Metrik                              | Typ       | Zweck                                        |
|-------------------------------------|-----------|----------------------------------------------|
| `kb_ticks_total{outcome}`           | counter   | Ticks nach Ergebnis: `executed`, `hold`, `blocked`, `error`, `skipped_setup` |
| `kb_trades_total{action}`           | counter   | Tatsächlich ausgeführte Trades pro Aktion    |
| `kb_last_tick_timestamp_seconds`    | gauge     | Letzter Tick als Unix-Timestamp              |
| `kb_next_tick_timestamp_seconds`    | gauge     | Geplanter nächster Tick (bei Scheduler-Query aktualisiert) |
| `kb_scheduler_running`              | gauge     | 1 = Scheduler läuft                          |
| `kb_scheduler_paused`               | gauge     | 1 = Scheduler-Job pausiert                   |
| `kb_http_requests_total{...}`       | counter   | HTTP-Requests nach Methode/Route/Status      |
| `kb_http_request_duration_seconds`  | histogram | HTTP-Latenz-Verteilung                       |

Beispiel-Scrape-Config:

```yaml
scrape_configs:
  - job_name: kickbase
    metrics_path: /metrics
    static_configs:
      - targets: ["kb.lan:8000"]
```

### Log-Level

`KB_LOG_LEVEL=DEBUG` erhöht die Ausführlichkeit; im Dashboard-Log-Stream ist
Redaction weiter aktiv. Für stille Batch-Betrieb-Phasen `WARNING` setzen.

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
