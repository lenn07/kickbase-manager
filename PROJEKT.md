# Kickbase Auto-Manager — Projekt-Konzept

> Dockerisierter, KI-gestützter Auto-Manager für [Kickbase](https://www.kickbase.com/) (Bundesliga Fantasy Manager).
> Der Container läuft dauerhaft, meldet sich mit den Zugangsdaten des Nutzers an, wählt eine Liga aus und
> führt in einem konfigurierbaren Intervall (z. B. alle 2 h) die aktuell **best mögliche Transfer-Aktion**
> auf Basis von Transfermarkt, eigenem Kader, kommenden Spieltagen und Marktwert-Trends aus.

---

## 1. Machbarkeitsanalyse

### 1.1 Ergebnis
**Technisch machbar — ja.** Die Kickbase-Mobile-App kommuniziert über eine dokumentierte REST-API
(v4). Es existieren mehrere reverse-engineerte, öffentlich gepflegte Referenzen:

| Ressource | Zweck |
|-----------|-------|
| [`kevinskyba/kickbase-api-doc`](https://github.com/kevinskyba/kickbase-api-doc) | OpenAPI-Spezifikation aller bekannten v4-Endpunkte (aktiv gepflegt, Stand 03/2026) |
| [`kevinskyba/kickbase-api-python`](https://github.com/kevinskyba/kickbase-api-python) | Python-Wrapper (`pip install Kickbase-API`) mit Login, Markt, Kauf/Verkauf |
| [`UtoPiiAx/KickbaseBot`](https://github.com/UtoPiiAx/KickbaseBot) | Referenzimplementierung Auto-Bidder |
| [`maximilian-karle/Kickbase-AI-Manager`](https://github.com/maximilian-karle/Kickbase-AI-Manager) | KI-Assistent via Model Context Protocol (MCP) |
| [`simonsagstetter/kickbase-api-v4-docs`](https://github.com/simonsagstetter/kickbase-api-v4-docs) | Postman/Swagger Collection (archiviert, aber brauchbar) |

### 1.2 Nachgewiesene Endpunkte (Auswahl)
- `POST /user/login` — Auth (Bearer-Token, TTL ~ Tage)
- `GET /leagues/{lid}/market` — aktueller Transfermarkt
- `POST /leagues/{lid}/market` — eigenen Spieler zum Verkauf anbieten (`playerId`, `price`)
- `POST /leagues/{lid}/market/{pid}/offers` — Gebot auf Marktspieler abgeben
- `POST /leagues/{lid}/market/{pid}/offers/{oid}/accept|decline` — eingehende Gebote verwalten
- `GET /leagues/{lid}/me` — Budget, Kaderwert, TeamValue
- `GET /leagues/{lid}/squad` — eigener Kader
- `GET /competitions/1/matchdays` — Spielplan/Spieltage
- `GET /players/{pid}/marketvalue/{n}` — historische Marktwerte

### 1.3 Risiken & Constraints
| Risiko | Bewertung | Gegenmaßnahme |
|---|---|---|
| **Inoffizielle API** — kann sich ändern oder gesperrt werden | mittel | Wrapper kapseln, Version pinnen, Health-Check + Alerting |
| **AGB / ToS** — Automatisierung ist nicht offiziell erlaubt | hoch (rechtlich) | Nur Privatnutzung, konservatives Rate-Limit, User-Agent der App imitieren, explizites Disclaimer in README |
| **Bearer-Token-Refresh** — Session läuft ab | niedrig | Auto-Relogin bei `401`, Token verschlüsselt persistieren |
| **Rate-Limiting / Ban-Risiko** | mittel | Jitter zwischen Requests, Aktions-Budget pro Stunde begrenzen, „menschliche" Zeiten (nicht nachts) |
| **KI-Fehlentscheidung** (Fehlkauf) | mittel | Dry-Run-Modus, max. Budget-Anteil pro Trade, Blacklist, User-Confirmation-Modus optional |
| **Marktwert-Volatilität** | niedrig | 7-Tage-Historie als Feature, Trend-Score |

### 1.4 Rechtlicher Hinweis
Kickbase bietet keine offizielle Automatisierungs-API. Der Betrieb erfolgt **auf eigenes Risiko**;
ein Account-Ban ist bei aggressivem Traffic möglich. Das Projekt ist ausdrücklich für den
**privaten, nicht-kommerziellen Einzelaccount-Betrieb** gedacht.

---

## 2. Anforderungen

### 2.1 Funktional (MUST)
1. **F-1 Setup-Wizard (Erststart)** — Geführter 3-Schritt-Setup: (a) Kickbase-Login (E-Mail/Passwort), (b) Anthropic-API-Key eingeben **und via Test-Call verifizieren** (`POST /v1/messages` mit Mini-Prompt, HTTP-200 = grün), (c) SMTP-Config für E-Mail-Benachrichtigungen. Ohne alle drei validen Schritte bleibt die App im Setup-Modus (Auto-Loop deaktiviert).
2. **F-2 Liga-Auswahl** — Nach Kickbase-Login werden alle Ligen des Users gelistet; **eine** wird als aktiv markiert (Single-User, Single-League).
3. **F-3 Intervall-Konfiguration** — Nutzer setzt das Analyse-Intervall (Default 2 h; Range 15 min – 24 h).
4. **F-4 Auto-Trade-Loop** — Container-interner Scheduler triggert im Intervall den Entscheidungs-Job.
5. **F-5 KI-Entscheidung** — Pro Tick wird **max. eine** Aktion ausgeführt: Kauf, Verkauf, Angebot annehmen/ablehnen oder `HOLD`.
6. **F-6 Dashboard** — Live-Status (Budget, Kader, letzte Aktion, nächste Ausführung), Aktions-Historie, Logs.
7. **F-7 Dry-Run** — Toggle: Entscheidungen werden geloggt + per E-Mail gemeldet, aber **nicht** an Kickbase gesendet.
8. **F-8 Notfall-Stop** — Kill-Switch in UI und via `SIGTERM`.
9. **F-9 E-Mail-Benachrichtigung** — Nach jeder ausgeführten Aktion (Kauf/Verkauf/Angebot) sowie bei Fehlern (Login-Fail, API-Down, LLM-Fehler) → E-Mail an konfigurierte Adresse. `HOLD`-Ticks werden gebündelt als Tages-Digest (opt-in) verschickt, nicht einzeln.

### 2.2 Funktional (SHOULD)
- **F-10** Konfigurierbare Guardrails: max. Budget pro Trade (%), Mindest-Cash-Reserve, Spieler-Blacklist/Whitelist.
- **F-11** Backtesting-Modus über historische Marktwerte.
- **F-12** „Test-E-Mail"-Button im Settings-Screen zur SMTP-Validierung.

### 2.3 Nicht-funktional
| Kategorie | Anforderung |
|---|---|
| **Zielplattform** | **Raspberry Pi (ARM64/ARMv7)** primär; AMD64 als Sekundärziel. Multi-Arch-Build via `docker buildx`. |
| **Persistenz** | SQLite-Volume; keine externe DB nötig (SD-Karten-freundlich: `PRAGMA journal_mode=WAL`). |
| **Sicherheit** | Kickbase-Passwort und Anthropic-Key **nie** im Klartext auf Disk; Fernet-Encrypt mit Master-Key aus gemountetem File; UI hinter Cookie-Session-Auth. |
| **Beobachtbarkeit** | Strukturiertes JSON-Logging (stdout), `/health`, `/metrics` (Prometheus-Format). |
| **Ressourcen** | **< 200 MB RAM idle**, **< 3 % CPU idle** auf Pi 4 — Pi Zero 2 W ist Stretch-Goal. Kein pandas/scikit im Runtime-Image (Heuristik mit Bordmitteln). |
| **Zeitzone** | Europe/Berlin (konfigurierbar). |
| **Testing** | ≥ 70 % Line-Coverage Kern-Logik; API-Layer via Recorded-Fixtures (VCR). |

### 2.4 Out-of-Scope (v1)
- Multi-Account / Multi-Liga parallel
- Mobile-App
- Bezahl-Features / SaaS-Deployment
- Live-Lineup-Optimierung (nur Transfers, keine Startelf-Rotation)

---

## 3. Architektur-Entscheidungen (ADR-Style, kompakt)

### ADR-1: Sprache & Runtime → **Python 3.12**
**Warum:** Reifster Kickbase-Wrapper existiert in Python; Data-Science-Stack (pandas, scikit-learn)
für Marktwert-Trends direkt verfügbar; Anthropic-SDK erstklassig.
**Trade-off:** TypeScript hätte einheitliches Frontend/Backend erlaubt, aber Wrapper müsste selbst gebaut werden.

### ADR-2: Web-Framework → **FastAPI + Jinja2/HTMX**
**Warum:** Async-fähig (WebSocket für Live-Dashboard), OpenAPI out-of-the-box, minimaler Overhead.
HTMX statt SPA vermeidet separaten Frontend-Build-Step → **ein** Docker-Image.
**Trade-off:** Keine reiche Client-Interaktivität; für das MVP-Dashboard ausreichend.

### ADR-3: Scheduling → **APScheduler (in-process)**
**Warum:** Kein Broker (Redis/Celery) nötig → Single-Container-Deployment bleibt intakt.
Persistente Jobstores via SQLite unterstützt.
**Trade-off:** Kein horizontales Scaling — für Single-User-Use-Case irrelevant.

### ADR-4: Persistenz → **SQLite + SQLModel**
**Warum:** Datei-basiert, Volume-Mount reicht; SQLModel = Pydantic + SQLAlchemy → typsicher.
**Trade-off:** Keine Concurrent-Writes-Skalierung; für Einzelnutzer belanglos.

### ADR-5: Entscheidungs-Engine → **hybrid: Regel-Heuristik + LLM-Kurator (LLM PFLICHT)**
**Schicht 1 (deterministisch, schnell):** Score pro Spieler
`score = w1·form + w2·restspielplan_leicht + w3·marktwert_trend − w4·verletzung − w5·preis/leistung`.
Top-N-Kandidaten (Kauf) und Bottom-N eigener Kader (Verkauf) werden vorgefiltert.
`HOLD` gewinnt, wenn kein Kandidat `min_action_score` erreicht.
**Schicht 2 (LLM, teuer, selten):** Claude bekommt die vorgefilterten 10–20 Optionen +
Ligakontext und wählt begründet **eine** Aktion (inkl. `HOLD`) → strukturierter JSON-Output.
**Warum hybrid:** Rein regelbasiert = starr, rein LLM = teuer & unverlässlich bei Zahlen.
Hybrid nutzt Stärken beider; LLM-Kosten pro Tick << 1 ct.
**Warum LLM Pflicht:** Nutzeranforderung — ohne validierten Anthropic-Key bleibt die App im Setup-Modus,
Auto-Loop startet nicht. Kein „Heuristik-only-Fallback"-Modus im MVP (weniger Config, klarere Semantik).
**Trade-off:** Zwei Systeme zu warten; Regel-Gewichte müssen kalibriert werden.

### ADR-6: LLM-Provider → **Anthropic Claude (Sonnet 4.6)**
**Warum:** Beste Reasoning-Leistung für strukturierte Entscheidungen bei moderaten Kosten;
zuverlässiger Tool-Use / JSON-Mode; Nutzer verwendet ohnehin Claude-Ökosystem.
**Key-Verwaltung:** Über UI-Setup-Wizard eingegeben, Test-Call gegen `POST /v1/messages` (min. Prompt,
`max_tokens=10`) validiert Key **vor** Persistenz. Bei Rotation gleicher Flow.
**Trade-off:** Vendor-Lock-in — abgemildert durch dünne `LlmClient`-Abstraktion.

### ADR-10: Notifications → **SMTP (E-Mail), kein Webhook**
**Warum:** Nutzeranforderung; SMTP läuft überall (Gmail-App-Password, Mailjet, eigener MTA) — keine
zusätzliche SaaS-Bindung. `aiosmtplib` ist async, ~50 kB, keine Compile-Deps → Pi-freundlich.
**Trigger-Events:** ausgeführte Trade-Aktionen (immer), Fehler (Login-Fail, API-5xx, LLM-Down),
optional Tages-Digest der `HOLD`-Ticks.
**Trade-off:** Keine Push-Benachrichtigung aufs Handy ohne Zusatz — akzeptabel; E-Mail wird auf Mobil ohnehin gepusht.

### ADR-11: Zielplattform → **Raspberry Pi (Multi-Arch Docker Image)**
**Warum:** Explizites Deployment-Ziel des Nutzers.
**Konsequenzen:**
- `docker buildx build --platform linux/arm64,linux/amd64` in CI.
- **Keine schweren Native-Deps** (pandas, numpy, scikit-learn) im Runtime-Image → Heuristik in reinem Python;
  vermeidet lange ARM-Wheel-Builds und hält Image klein.
- SQLite mit WAL-Mode (SD-Karten-schonender als Rollback-Journal).
- Log-Rotation zwingend (SD-Karten-Lifetime) — `RotatingFileHandler` mit 5 × 2 MB.
**Trade-off:** Kein ML-Modell-Training im Container möglich; für aktuellen Scope ausreichend.

### ADR-7: Deployment → **Single Docker Image, Multi-Stage Build**
**Warum:** Ein `docker run -p 8000:8000 -v kb_data:/data kickbase-manager` genügt.
Multi-Stage hält Image < 200 MB (python:3.12-slim Base).
**Trade-off:** UI, Scheduler und API im selben Prozess-Baum → gemeinsame Fehlerquelle;
mit sauberer Task-Isolation (APScheduler-Executor-Pools) beherrschbar.

### ADR-8: Konfiguration → **12-Factor via `pydantic-settings`**
Alle Settings via ENV-Variablen; Overrides via Web-UI persistiert in DB.

### ADR-9: Sicherheit — Credentials → **Fernet-Encryption at rest**
Master-Key wird beim ersten Start generiert und in Docker-Secret / gemountetem File erwartet.
Kein Klartext-Passwort in DB oder Logs.

---

## 4. System-Architektur

```
┌────────────────────────── Docker Container ──────────────────────────┐
│                                                                       │
│  ┌─────────────┐   HTTP    ┌──────────────────────────────────────┐  │
│  │  Browser    │──────────▶│  FastAPI App                         │  │
│  │  (User UI)  │  HTMX/WS  │  ├── /login   /leagues   /dashboard  │  │
│  └─────────────┘           │  ├── /settings   /history   /health  │  │
│                            │  └── WebSocket: Live-Log-Stream      │  │
│                            └──────────┬───────────────────────────┘  │
│                                       │                              │
│                            ┌──────────▼──────────┐                   │
│                            │  Application Core   │                   │
│                            │  ┌──────────────┐   │                   │
│                            │  │ AuthService  │───┼──▶ KickbaseClient │
│                            │  ├──────────────┤   │      (HTTPX)     │
│                            │  │ LeagueSvc    │   │        │         │
│                            │  ├──────────────┤   │        ▼         │
│                            │  │ DecisionEng. │───┼──▶ Kickbase v4 API│
│                            │  │  ├─Heuristik │   │  (api.kickbase.  │
│                            │  │  └─LlmClient │───┼──▶  com)         │
│                            │  ├──────────────┤   │                   │
│                            │  │ TradeExec.   │   │                   │
│                            │  └──────────────┘   │                   │
│                            └──────────┬──────────┘                   │
│                                       │                              │
│                    ┌──────────────────┼──────────────┐               │
│                    ▼                  ▼              ▼               │
│           ┌───────────────┐  ┌────────────────┐  ┌──────────┐        │
│           │ APScheduler   │  │ SQLite         │  │ Logger   │        │
│           │ (interval job)│  │ /data/kb.db    │  │ (JSON)   │        │
│           └───────────────┘  └────────────────┘  └──────────┘        │
└───────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
                              ┌────────────────┐
                              │ Docker Volume  │
                              │  kb_data       │
                              └────────────────┘
```

### 4.1 Modul-Layout (Clean Architecture)
```
app/
├── domain/              # Reine Business-Entities, keine Framework-Deps
│   ├── player.py
│   ├── league.py
│   ├── trade_decision.py
│   └── scoring.py       # Heuristik-Score (pure functions)
├── application/         # Use Cases (orchestriert domain + infra)
│   ├── login_uc.py
│   ├── evaluate_market_uc.py
│   └── execute_trade_uc.py
├── infrastructure/      # Adapter → externe Systeme
│   ├── kickbase/        # HTTP-Client + DTO-Mapper
│   ├── llm/             # Anthropic-Adapter
│   ├── persistence/     # SQLModel + Repositories
│   └── scheduler/       # APScheduler-Setup
├── interface/           # FastAPI Routen + HTMX-Templates
│   ├── web/
│   └── api/
└── main.py              # Composition Root (Dependency Injection)
```
**Regel:** Abhängigkeiten zeigen **immer nach innen** (Interface → Application → Domain).
`domain/` importiert nichts aus `infrastructure/` oder Frameworks.

### 4.2 Datenmodell (SQLite, vereinfacht)
```
users(id, email, encrypted_password, kb_token, kb_token_exp, created_at)
leagues(id, user_id, kb_league_id, name, is_active)
credentials(id, user_id, kind, encrypted_value, verified_at)  -- kind: 'anthropic', 'smtp'
smtp_config(id, user_id, host, port, username, encrypted_password,
            from_addr, to_addr, use_tls)
settings(id, user_id, interval_min, dry_run, max_trade_pct,
         min_cash_reserve, min_action_score, blacklist_json,
         digest_enabled, digest_hour)
trade_log(id, ts, action, player_id, player_name, price,
          reason_text, executed, response_code, notified_at)
market_snapshot(id, ts, league_id, payload_json)   -- für Backtest
```

---

## 5. Roadmap

| Phase | Deliverable | Aufwand grob |
|---|---|---|
| **0. Setup** | Repo-Skeleton, Dockerfile, CI (ruff+pytest), pre-commit | 0,5 d |
| **1. Kickbase-Adapter** | Login, Squad, Market, Trades gekapselt + VCR-Tests | 1,5 d |
| **2. UI-Login-Flow** | FastAPI + HTMX-Login, Liga-Wahl, Settings-Page | 1 d |
| **3. Scheduler-Loop** | APScheduler + Dry-Run-Executor + trade_log | 0,5 d |
| **4. Heuristik-Engine** | Score-Modell + Kandidaten-Filter + Unit-Tests | 1,5 d |
| **5. LLM-Kurator** | Claude-Prompt-Design, JSON-Schema-Output, Guardrails | 1 d |
| **6. Dashboard** | Live-Status, History-View, WebSocket-Log-Stream | 1 d |
| **7. Hardening** | Encryption, Rate-Limit, Health/Metrics, Docs | 1 d |
| **Summe MVP** | | **≈ 8 Personentage** |

---

## 6. Code-Konventionen

- **Style:** `ruff format` + `ruff check --select ALL` (mit begründeten `# noqa`).
- **Types:** `mypy --strict` für `domain/` und `application/`; `--ignore-missing-imports` für Infra.
- **Tests:** `pytest` + `pytest-asyncio`; VCR-cassettes für Kickbase-Calls; kein Netzwerk in CI.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `refactor:`…).
- **Branching:** Trunk-based; PR-Reviews via `gh pr create`.
- **Docs:** Jedes Modul hat einen Doku-Header (Zweck + Verantwortlichkeit in ≤ 3 Sätzen).
- **SOLID:** DIP über Protokoll-Klassen (`KickbaseGateway`, `LlmGateway`) → Tests mocken Protokolle.

---

## 7. Getroffene Entscheidungen & offene Fragen

### Entschieden
- **Aggressivität:** Der Bot darf **bewusst warten** — „no-op" ist eine gleichwertige Option der Entscheidungs-Engine.
  Konsequenzen für die Umsetzung:
  - `TradeDecision` kennt den Typ `HOLD` (kein Kauf, kein Verkauf, kein Angebot).
  - Die Heuristik-Schicht liefert einen **Mindest-Score-Schwellenwert** (`min_action_score`, konfigurierbar);
    wird er von keinem Kandidaten überschritten → automatisch `HOLD`.
  - Der LLM-Kurator bekommt `HOLD` explizit als Option ins Prompt inkl. Kriterien
    („warten, wenn alle Kandidaten <5 % erwarteter Mehrwert / Marktwert bringen").
  - `HOLD`-Ticks werden im `trade_log` erfasst (mit Grund), damit im Dashboard sichtbar bleibt,
    **warum** nichts passiert ist.

- **LLM:** Anthropic-Key wird über UI eingegeben und via Test-Call verifiziert; ohne validen Key kein Auto-Loop. Kein Heuristik-only-Fallback.
- **Multi-User:** Nein — **Single-User, Single-League** pro Container.
- **Notifications:** **E-Mail via SMTP** (siehe ADR-10).
- **Hosting:** **Raspberry Pi** — Multi-Arch-Image (arm64 + amd64), Ressourcen-Budget < 200 MB RAM (siehe ADR-11).

### Noch offen
_(keine — alle Setup-Fragen geklärt, Umsetzung kann starten)_

---

## 8. Referenzen

- Kickbase-API-Dokumentation (inoffiziell): <https://kevinskyba.github.io/kickbase-api-doc/index.html>
- Python-Wrapper: <https://github.com/kevinskyba/kickbase-api-python>
- OpenAPI-Spec: <https://github.com/kevinskyba/kickbase-api-doc/blob/master/openapi_spec.json>
- Referenz-Bot: <https://github.com/UtoPiiAx/KickbaseBot>
- KI-Referenz: <https://github.com/maximilian-karle/Kickbase-AI-Manager>
- Postman-Collection: <https://github.com/simonsagstetter/kickbase-api-v4-docs>

---
*Version 0.1 — 2026-08-30*
