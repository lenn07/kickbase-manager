# Kickbase Auto-Manager

Dockerisierter, KI-gestützter Auto-Manager für [Kickbase](https://www.kickbase.com/).
Details zu Anforderungen, Architektur und Roadmap in [`PROJEKT.md`](./PROJEKT.md).
Der laufende Optimierungsplan (Phasen P0–P2, Fortschritts-Board, verifiziertes Kickbase-Regelwerk,
Defekt-Register) steht in [`docs/optimizing_plan.md`](./docs/optimizing_plan.md).
Einstieg aus einem neuen Chat: **„starte Phase 0 des Optimizing-Plans"**.

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
  -e KB_LINEUP_WRITES_ENABLED=false \
  -e KB_CLUB_LIMIT=unlimited \
  -e KB_UNDERPAY_BLOCKED=true \
  -e KB_SCORING_MODE=season_points \
  --restart unless-stopped \
  kickbase-auto-manager
```

### Liga-Einstellungen, die die API nicht herausgibt

Drei Liga-Regeln stehen in **keiner** Kickbase-Response: das Spielerlimit pro
Verein, die Admin-Option „Unterbieten deaktivieren" und der Wertungsmodus.
`GET /v4/leagues/{id}/settings` existiert nicht (HTTP 500 `NotFound`), und
`/me` liefert nur das Kaderlimit und die aktuelle Verteilung je Verein.

Alle drei stehen in der App unter **Liga → Admin-Einstellungen** und werden
über ENV nachgereicht:

| Variable | Werte | Wirkung |
|---|---|---|
| `KB_CLUB_LIMIT` | `1`…`11` oder `unlimited` | Wie viele Spieler desselben Vereins erlaubt sind, offene Gebote eingerechnet |
| `KB_UNDERPAY_BLOCKED` | `true` / `false` | Bei `true` ist **jedes** Gebot unter Marktwert blockiert, nicht erst eins unter Marktwert − 10 % |
| `KB_SCORING_MODE` | `season_points` / `head_to_head` | Head-to-Head belohnt Varianz gegen starke Gegner, Saisonpunkte nicht |

Ohne Eintrag gelten sie als **unbekannt** — nicht als „unbegrenzt" oder
„erlaubt". Das Modell bekommt dann `null` plus ein `missing_data`-Flag und
verhält sich zurückhaltend: es behauptet keinen Regelverstoß, den es nicht
kennen kann, bietet aber auch nicht unter Marktwert. `unlimited` ist deshalb
etwas anderes als Weglassen: es ist eine Antwort.

### Aufstellungs-Writes

`KB_LINEUP_WRITES_ENABLED` steuert die einzige Aktion, die **unmittelbar
Punkte bewegt**: das Schreiben der Startelf. Default ist `false`.

Bei `false` läuft der Startelf-Guard trotzdem mit und legt jede Aufstellung,
die er gesetzt hätte, als `SET_LINEUP`-Zeile mit `executed=false` ins
`trade_log` — inklusive Formation und Spieler-IDs. Vergleiche diese Einträge
eine Woche lang mit der Kickbase-App; erst dann auf `true` stellen.

Zur Einordnung: jeder unbesetzte Startelf-Slot kostet **100 Punkte** pro
Spieltag. Ein Kader mit acht Spielern verliert also 300 Punkte, die kein
späterer Tick zurückholt — der Guard kann aber nur besetzen, was im Kader
steht. Die fehlenden Plätze füllt nur ein Kauf.

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
