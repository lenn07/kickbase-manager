# Optimizing-Plan — Kickbase Auto-Manager

> **Zweck dieser Datei:** Sie ist die vollständige, selbsttragende Arbeitsgrundlage, um den Bot vom
> heutigen Zustand auf Profi-Niveau zu heben. Ein frischer Chat braucht **nur diese Datei** —
> keine erneute Recherche, kein erneutes Code-Studium.
>
> **Erstellt:** 2026-09-23 · **Basis:** Recherche zu Kickbase-Regeln (offizielle Hilfe, Stand
> Saison 26/27) + vollständiger Code-Audit + echte API-Payloads aus `tests/infrastructure/kickbase/cassettes/`.

---

## 0. Wie diese Datei benutzt wird

### 0.1 Einstieg aus einem neuen Chat

| Du sagst | Ich tue |
|---|---|
| `starte phase 0 des optimizing plans` | Lies §1 (Board) → nimm das erste offene Paket aus Phase 0 → arbeite es nach §6 ab → aktualisiere §1 und §9 |
| `starte P0-3` | Springe direkt zu diesem Paket (prüfe vorher die Abhängigkeiten in §7) |
| `status des optimizing plans` | Gib §1 wieder + nenne das nächste Paket |
| `weiter` | Nimm das nächste offene Paket in Reihenfolge |

**Erste Handlung in jeder Session:** Diese Datei lesen, danach §1 (Board) und §8 (Offene Fragen)
prüfen — offene Fragen blockieren manche Pakete.

### 0.2 Arbeitsweise (gilt für jedes Paket)

1. **Ein Paket = ein Branch = ein Merge.** Nie zwei Pakete gleichzeitig. Branch-Name: `opt/<paket-id>`,
   z. B. `opt/p0-1-team-value`.
2. **Daten vor Prompt.** `docs/master_prompt.md` wird in einem Block immer **zuletzt** angefasst —
   erst wenn die Felder, die er referenziert, im USER-JSON stehen.
3. **Gate vor jedem Merge:**
   ```bash
   ruff check . && ruff format --check . && mypy && pytest
   ```
4. **Review-Artefakt ist der Payload-Diff**, nicht der Code-Diff. Nach jedem Paket:
   *Was sieht das LLM jetzt zusätzlich?* (Snapshot-Test aus P0-0.4.)
5. **Nach jedem Paket:** §1 Board aktualisieren, §9 Änderungslog ergänzen, bei neuen Erkenntnissen
   §3/§8 nachziehen. Das ist Teil des Pakets, nicht optional.

### 0.3 Fortschritts-Legende

`[ ]` offen · `[~]` in Arbeit · `[x]` gemerged · `[!]` blockiert (Grund dahinter notieren)

---

## 1. Fortschritts-Board

### Phase 0 — Discovery & Guardrails · Status: **offen**
- [x] **P0-0.1** Endpoint-Discovery erweitern (`scripts/inspect_endpoints.py`)
- [ ] **P0-0.2** Die 3 offenen Fragen beantworten (→ §8)
- [ ] **P0-0.3** Cassettes neu aufnehmen (inkl. aktivem Listing + Gebot)
- [ ] **P0-0.4** Payload-Snapshot-Test bauen
- [ ] **P0-0.5** Contract-Drift-Checker (`scripts/check_contract.py`)

### Phase 1 — P0: Bot handlungsfähig machen · Status: **offen**
- [ ] **P0-1** Team-Value & Markt-Metadaten (`tv`, `mvud`, `dt`)
- [ ] **P0-2** Gebote parsen (Offers-Array)
- [ ] **P0-3** Marktspieler-Leistungsdaten + `prob`
- [ ] **P0-4** Aufstellung setzen (Guard + `SET_LINEUP`)
- [ ] **P0-5** Master-Prompt korrigieren

### Phase 2 — P1: Von „funktioniert" auf „gut" · Status: **offen**
- [ ] **P1-6** Kaufpreis & G/V aus Kickbase (`prc`, `mvgl`)
- [ ] **P1-7** Trends aus Payload statt 25 HTTP-Calls (`tfhmvt`, `sdmvt`)
- [ ] **P1-8** Echte Form & Minuten (`/performance`)
- [ ] **P1-9** Liga-Settings lesen (Kader-/Vereinslimit, Underpay, Modus)
- [ ] **P1-10** Scheduler auf Ereignis-Fenster umstellen

### Phase 3 — P2: Top-Niveau · Status: **offen**
- [ ] **P2-11** Spielplan & Gegnerstärke (FDR)
- [ ] **P2-12** Ligakontext (Rang, Rückstand, H2H-Gegner)
- [ ] **P2-13** Overbid-Kalibrierung über `offer_count`
- [ ] **P2-14** Saisonphasen-Gewichtung Trading ↔ Punkte
- [ ] **P2-15** Täglicher Bonus (`/v4/bonus/collect`)
- [ ] **P2-16** Mehrere Aktionen pro Tick im Deadline-Fenster

### Messbare Erfolgskriterien (Definition of Done je Phase)

| Phase | Messbar fertig, wenn … |
|---|---|
| **0** | `pytest` grün · Snapshot-Datei existiert · alle 3 Fragen in §8 beantwortet |
| **1** | Die 4 Gap-Assertions aus P0-0.4 sind grün · Eval-Suite grün · 7 Tage Shadow (`dry_run=true`) ohne Executor-Fehler im `trade_log` |
| **2** | HTTP-Calls/Tick gesunken (Messung im Log) · `avg_points_last5` ist echte L5 · keine hartkodierten Limits mehr in `kb_rules.py` · Ticks feuern in den Fenstern aus P1-10 |
| **3** | USER-JSON enthält Gegnerstärke + Ligarang · Overbid ist formelbasiert · Deadline-Tick kann ≥ 2 Aktionen ausführen |

---

## 2. Verifiziertes Regelwerk (Wissensbasis — nicht neu recherchieren)

> Stand 2026-09-23, Saison **26/27**, Modus **Classic/Seasonal** (der einzige Modus mit Transfermarkt).
> Quellen: help.kickbase.com (jeweils direkt geprüft), us.kickbase.com/points-table.

### 2.1 Siegbedingung
- **Saisonpunkte** (Standard): Summe aller Spieltagspunkte gewinnt.
- **Head-to-Head** (neu 26/27): pro Spieltag Duell, 3/1/0, max. 18 Manager, bei Gleichstand entscheiden
  kumulierte Punkte. → anderes Risikoprofil (gegen starke Gegner Varianz suchen).
- Teamwert ist **nie** Siegkriterium, nur Mittel zum Zweck.
- **Captain-Feature** (Punkte ×2) existiert seit 26/27, aber **nicht in Classic** — nur Arena/Rush. Irrelevant für diesen Bot.

### 2.2 Harte Regeln
1. **Konto ≥ 0 zum Anpfiff** (i. d. R. Fr 20:30, englische Woche Di 18:30; es zählt die *angesetzte* Zeit).
   Negativ → **0 Punkte für den ganzen Spieltag**.
2. **11 aufgestellte Spieler**, Aufstellung friert 20:29. Jede leere Position = **−100 Punkte**.
   Nur aufgestellte Spieler punkten.
3. **33 %-Regel:** max. Minus = `0,33 × (Mannschaftswert + min(0, Kontostand))`.
   **Offene Gebote zählen voll mit** — sonst blockt Kickbase das Gebot.
4. **Kaderlimit 11–25** (Admin-Einstellung).
5. **Spielerlimit pro Verein 1–11** (Admin-Einstellung — *kein* fixes „max. 3"!). Offene Gebote zählen mit.
6. **Gebot < Marktwert − 10 % ist nicht absetzbar.** Mit Admin-Option „Underpay deaktivieren" sind
   alle Gebote < MW blockiert (bei neuen Ligen standardmäßig aus).
7. **Maßgeblich ist der Marktwert zum Transferzeitpunkt**, nicht zur Gebotsabgabe.
   Steigt der MW über dein Gebot → Gebot wird abgelehnt.
8. **Gleichstand → früheres Gebot gewinnt.**
9. **Sofortverkauf = voller aktueller Marktwert**, sofort gutgeschrieben, irreversibel.
   ⚠️ Der aktuelle Master-Prompt behauptet fälschlich „meist unter Marktwert".

### 2.3 Marktwert-Mechanik
- **Update täglich ~22:00 Uhr** (Europe/Berlin) im Seasonal-Modus. Einziger wirtschaftlich relevanter
  Zeitpunkt des Tages. API liefert ihn als `mvud` im Market-Response.
- Treiber laut Kickbase, in dieser Reihenfolge:
  1. **Nachfrage der Gesamt-Community über alle Ligen** (Käufe ↑ / Verkäufe + Listungen ↓)
  2. Form/Punkte
  3. erwartete Einsatzzeit (Verletzung, Sperre, Trainingsnews)
- → **Der Marktwert ist ein Nachfrage-Indikator, kein Leistungs-Indikator.** Man handelt die Erwartung,
  was die Masse morgen tut.
- Untergrenze **500.000 €**.
- Kickbase listet eigene Spieler **exakt zum Marktwert** (empirisch: `prc == mv` bei allen 22 Einträgen
  der Cassette). Listing-Laufzeiten real gemessen: **0,8 h – 33 h** (`exs`).
- Findet sich kein menschlicher Bieter, **bietet Kickbase selbst ~MW** (Quellen: ±1 % bis +1–10 %).
  → Ein Listing zu ~MW hat eine Untergrenze nahe MW und ist dem Sofortverkauf überlegen, wenn Zeit da ist.

### 2.4 Punktesystem (Größenordnungen, die Entscheidungen steuern)

| Aktion | TW | ABW | MF | ST |
|---|---|---|---|---|
| Tor | +120 | +100 | +90 | +80 |
| Assist | +55 | +45 | +35 | +35 |
| **Zu Null** (pro 10 min + Bonus) | ~+55 | ~+33 | ~+22 | ~+11 |
| **Spiel gewonnen / verloren** | ±15 | ±15 | ±15 | ±15 |
| Startelf | +5 | +5 | +5 | +5 |
| Einsatzminuten | +1 je 10 min | | | |
| Teamtor | +5 | +5 | +5 | +5 |
| Gegentor | −5 | −5 | −5 | −5 |
| Gelb / Gelb-Rot / Rot | −10 / −50 / −75 | | | |
| Fehler vor Gegentor · Elfmeter verschuldet | −45 | | | |
| Elfmeter verschossen · Eigentor | −60 | | | |

**Ableitungen, die in den Prompt gehören:**
- IV eines Top-Teams, Heimsieg 2:0 zu Null ⇒ ~+72 Punkte **ohne eine einzige Offensivaktion**.
  Defensive starker Teams gegen schwache Gegner ist die verlässlichste Punktequelle im Spiel.
- Ergebnis + Gegentore korrelieren innerhalb eines Teams perfekt ⇒ 4 Spieler eines Clubs sind
  kein Portfolio, sondern eine gehebelte Wette auf ein Spiel.
- **Minuten sind die Basis von allem** ⇒ Startelf-Wahrscheinlichkeit schlägt Form.
- „Rohpunkte" (Zweikämpfe/Pässe/Ballgewinne) sind repetierbar, Tore/Assists volatil.

### 2.5 Geldquellen neben dem Trading
- **Spieltagsprämie** auf Punkte (offiziell bestätigt; Betrag nicht offiziell publiziert,
  Community nennt ~10.000 €/Punkt in Classic — **nicht als Fakt behandeln**).
- **Spieltags-Erfolge** gestaffelt (≥1.000 / 1.500 / 2.000 Punkte), Spieltagssieger 1 Mio.
- **Transfer-Erfolge:** Bronzenes Händchen 3 Mio Gewinn → 250k · Silbernes 5 Mio → 500k ·
  Goldenes 10 Mio → 1 Mio · Königstransfer 25 Mio → 2 Mio.
  **Nur über den Transfermarkt**, nicht bei zugelosten Spielern.
- **Teamwert-Erfolge** werden täglich ~22:00 geprüft und **nur ausgezahlt, wenn das Konto im Plus ist**.
- **Täglicher Login-Bonus**, eskalierend mit Streak, Reset bei Auslassen. API: `GET /v4/bonus/collect`.
- Meister 2 Mio / Vize 1 Mio pro Saison.

### 2.6 Profi-Entscheidungsmodell (Zielfunktion)

```
maximiere  Σ_Spieltage Punkte(Startelf)
u. d. N.   Konto(Anpfiff) ≥ 0
           |Startelf| = 11
           Minus ≤ 0,33 × (TV + min(0, Konto))
           Kader ≤ Limit, Spieler/Verein ≤ Limit
```

Trading erscheint hier **nicht als Ziel**, nur als Budget-Generator. Der Rechenweg:
*„Dieser Trade bringt X Gewinn. Wie viele Zusatzpunkte kaufen mir X über die Restsaison —
und was kostet mich der Trade zwischenzeitlich an Punkten?"*

| Saisonphase | Trading-Gewicht | Punkte-Gewicht | Grund |
|---|---|---|---|
| ST 1–8 | hoch | mittel | Kapital hat maximale Restlaufzeit, MW maximal volatil |
| ST 9–25 | mittel | hoch | Kader steht, Trading finanziert gezielte Upgrades |
| ST 26–34 | niedrig | maximal | MW-Gewinne haben keine Zeit mehr zu wirken |

**Kauf-Thesen (nie mischen):**
- *POINTS:* `xPts ≈ P(Startelf) × Form(L3) × Matchup(FDR 1–5) × Minuten-Erwartung`.
  Weitere Kriterien: Rotationsrisiko (Europapokal, englische Woche, 4. Gelbe), Standard-/Elfmeterschütze,
  Punkte pro Million als Effizienzfilter, **Positions-Arbitrage** (offensiver Spieler, den Kickbase
  als ABW führt → kassiert Zu-Null-Bonus *und* Offensivpunkte).
- *PROFIT:* erwartete Community-Nachfrage in 1–7 Tagen. Rückkehrer aus Verletzung, Vertreter bei
  Ausfällen, früh erkannte Rohpunkte-Sammler, Neuzugänge/Aufsteiger nahe 500k, antizyklisch kaufen.
  **Nicht kaufen, weil jemand gerade 200 Punkte gemacht hat** — dann ist die Nachfrage eingepreist.

**Verkaufs-Gründe (nur diese vier):**
1. Peak (7-d-Trend +, 1-d-Trend dreht; MW nahe `mv_max_30d` bei nachlassendem Momentum)
2. These gebrochen (Verletzung, Sperre, Rotation, Trainerwechsel, Auslandswechsel, schwerer Gegner)
3. Kapitalbedarf für ein Upgrade mit mehr xPts
4. Regel-Compliance (Konto vor Anpfiff ins Plus)

**Liquiditäts-Zyklus (der eigentliche Skill):**
```
Fr 20:30  Anpfiff → Konto MUSS ≥ 0, Aufstellung friert
Fr 20:31  Minus-Fenster offen → bis 33 % ins Minus
Sa–Do     gekaufte Spieler nehmen MW-Steigerungen mit (Update täglich 22:00)
Do/Fr     Positionen auflösen, Konto zurück ins Plus
Fr 20:30  Zyklus von vorn
```
Rückführung ins Plus **über Sofortverkäufe planen**, nicht über Listings (bis 33 h Laufzeit,
kein garantierter Zuschlag). Listing = Plan A mit Puffer, Sofortverkauf = garantierter Plan B.

**Der Kalender ist die Strategie:**

| Zeitpunkt | Was passiert | Was zu tun ist |
|---|---|---|
| täglich 21:45 | vor MW-Update | Gebote platzieren, Listings prüfen |
| täglich 22:15 | nach MW-Update | Gewinner/Verlierer auswerten, Positionen drehen |
| So 22:00 / Mo | Nachfragewelle auf Spieltagshelden | vorher gekauft haben, jetzt **nicht** kaufen |
| Mo 18:00 | finale Punkte + Prämien | Bilanz, Kader-Review |
| Do/Fr | Aufstellungsnews, Startelf-Prognosen | Startelf optimieren, Konto ins Plus |
| Fr 20:29 | Deadline | alles muss stehen |

---

## 3. API-Feldreferenz (aus echten Payloads verifiziert)

> Quelle: `tests/infrastructure/kickbase/cassettes/*.yaml` (Saison 26/27) +
> `kevinskyba/kickbase-api-doc` (OpenAPI/Swagger, Stand 03/2026, 147 Endpunkte).
> Der Swagger liegt bei Bedarf unter
> `https://raw.githubusercontent.com/kevinskyba/kickbase-api-doc/master/kickbase-v4.swagger.json`.

### 3.1 `GET /v4/leagues/{l}/market` — Root-Felder
```json
{"nps": 9, "tv": 135562602, "mvud": "2026-08-31T20:00:00Z",
 "dt": "2026-09-04T18:30:00Z", "day": 2, "sn": "26/27"}
```
| Feld | Bedeutung | Bot heute |
|---|---|---|
| `tv` | **Mannschaftswert** | ignoriert → `team_value` ist 0 (P0-1) |
| `mvud` | Zeitpunkt MW-Update (20:00 UTC = 22:00 Berlin) | ignoriert (P0-1 / P1-10) |
| `dt` | **Start des nächsten Spieltags** | ignoriert; stattdessen extra `list_matchdays()`-Call (P0-1) |
| `day` | Spieltagsnummer | ignoriert (P2-14: `matchdays_left`) |
| `nps` | Anzahl Spieler im eigenen Kader | ignoriert |
| `sn` | Saison | ignoriert |

### 3.2 `GET /v4/leagues/{l}/market` — Item-Felder
Verifizierte Keys: `i, fn, n, tid, pos, st, mv, mvt, p, ap, ofc, exs, prc, isn, iposl, dt, pim, prob`

| Feld | Bedeutung | Bot heute |
|---|---|---|
| `prob` | **Startelf-Wahrscheinlichkeit, 5 Stufen** (Cassette: `1`=6×, `2`=2×, `3`=2×, `4`=3×, `5`=9×; `1` bei Guirassy/Baku, `5` bei 500k-Ersatzkeeper ⇒ **vermutlich 1 = sicher Startelf**, in P0-0.2 zu bestätigen) | ignoriert; stattdessen pauschal 0.85 aus Verletzungsstatus (P0-3) |
| `p` / `ap` | Gesamt-/Ø-Punkte (in 14 von 22 Items vorhanden) | **hart auf 0 gesetzt** (P0-3) |
| `ofc` | **Anzahl Gebote** auf dieses Listing | ignoriert (P0-3 / P2-13) |
| `exs` | Sekunden bis Listing-Ablauf | genutzt ✅ |
| `prc` | Listing-Preis (bei Kickbase-Listings == `mv`) | genutzt ✅ |
| `mvt` | MW-Trendrichtung (0/1/2) | ignoriert |
| `isn` | „ist neu auf dem Markt" | ignoriert |
| `u` | Seller — String **oder** Objekt `{i, n, …}`; fehlt bei Kickbase-Listings | genutzt ✅ |
| **Offers-Array** | Feldname **unbekannt** — Cassette hatte überall `ofc: 0` | **P0-0.2 klären**, dann P0-2 |

### 3.3 `GET /v4/leagues/{l}/managers/{m}/squad` — Item-Felder
Verifizierte Keys: `pi, pn, tid, lo, lst, pos, st, stl, p, ap, iotm, sdmvt, tfhmvt, mvgl, mvt, mv, prc, pim`

| Feld | Bedeutung | Bot heute |
|---|---|---|
| `prc` | **Kaufpreis** (Doku-Beispiel: Grifo `prc` 32.000.122, `mv` 10.940.737, `mvgl` −21.059.385 ⇒ `mvgl = mv − prc`) | ignoriert; `models.py` behauptet fälschlich „kein Kaufpreis mehr" (P1-6) |
| `mvgl` | **unrealisierter Gewinn/Verlust** | ignoriert (P1-6) |
| `tfhmvt` | MW-Änderung **24 h** in € | ignoriert; stattdessen 1 HTTP-Call/Spieler (P1-7) |
| `sdmvt` | MW-Änderung **7 d** in € (Semantik in P1-7 empirisch gegen History validieren) | ignoriert (P1-7) |
| `lo` | Startelf-Slot 0..10 | genutzt ✅ |
| `lst` / `iotm` | gelistet / auf dem Transfermarkt | ignoriert |
| `st` / `stl` | Status + Status-Detailliste | `st` genutzt, `stl` ignoriert |

⚠️ **`st`-Enum unvollständig:** `PlayerStatus` kennt 0/1/2/4/8/16/32/64. In der API-Doku taucht
`st: 128` auf. `_to_status()` mappt Unbekanntes auf **FIT** → ein nicht spielberechtigter Spieler
sähe fit aus. (P0-3)

### 3.4 Ungenutzte Endpunkte mit hohem Wert
| Endpunkt | Liefert | Paket |
|---|---|---|
| `GET/POST /v4/leagues/{l}/lineup` | Aufstellung lesen **und setzen**. POST-Body: `{"type":"4-4-2","players":["1235", …]}` | P0-4 |
| `GET /v4/leagues/{l}/settings` | Liga-Settings (`amd`, `isp`, `gpm`, `lnm`, `mpst`, `mppu`) | P1-9 |
| `GET /v4/leagues/{l}/me` | `b` (Cash), `tpc[]` = **Spieler je Verein** (`{tid, npt}`) | P1-9 |
| `GET /v4/leagues/{l}/players/{p}/performance` | `it[].ph[]` mit `day, p` (Punkte), `mp` (**Minuten**), `md`, `t1/t2`, `st` | P1-8 |
| `GET /v4/leagues/{l}/players/{p}` | `sl` (Startelf-Prognose, Quelle `plpt`=„Ligainsider"), `mdsum[]` (**kommende Spiele**), `g`, `a`, `y`, `r`, `sec` | P2-11 |
| `GET /v4/leagues/{l}/ranking` | `us[]` mit `sp` (Saisonpunkte), `mdp`, `spl` (Platz), `tv`, **`lp[]` = Aufstellungen der Rivalen** | P2-12 |
| `GET /v4/competitions/1/table` | `tid, tn, cp, cpl, mc, gd, mdp, sp` → Gegnerstärke/FDR | P2-11 |
| `GET /v4/leagues/{l}/managers/{m}/transfer` | Transferhistorie: `pi, tty, trp` (Preis), `dt`, `othnm` | P1-6 (Fallback) |
| `GET /v4/leagues/{l}/activitiesFeed` | Aktivitäten → Budgets der Gegner schätzen | P2-12 (optional) |
| `GET /v4/bonus/collect` | täglicher Login-Bonus — ⚠️ **GET, aber wirkt wie ein Write** | P2-15 |

---

## 4. Defekt-Register (Code-Stand 2026-09-23, Commit `299b134`)

| # | Defekt | Ort | Wirkung | Paket |
|---|---|---|---|---|
| D1 | `team_value` wird nie gefüllt | `app/domain/models.py:73-74`, `app/application/run_tick_uc.py:139` | LLM liest `team_value: 0`, `max_negative_allowed: 0` ⇒ **33 %-Hebel komplett tot** | P0-1 |
| D2 | `offers` hart auf `()` | `app/infrastructure/kickbase/dto.py:237` | `incoming_offers` immer leer ⇒ `ACCEPT_OFFER`/`DECLINE_OFFER` unbenutzbar; gelistete Spieler nur per Sofortverkauf loszuwerden | P0-2 |
| D3 | `open_bids_total` immer 0 | `app/application/run_tick_uc.py:398` (Folge von D2) | 33 %-Check gegen offene Gebote läuft leer ⇒ stille Ablehnungen | P0-2 |
| D4 | Marktspieler ohne Leistungsdaten | `app/infrastructure/kickbase/dto.py:229-230` | `avg_points_last5: null` für **jeden** Marktspieler ⇒ Käufe ohne Datengrundlage | P0-3 |
| D5 | `prob` verworfen, Startelf-Prognose erfunden | `app/application/player_enrichment.py:49-60,137` | Ersatzkeeper und Kapitän bekommen beide `0.85` | P0-3 |
| D6 | Unbekannte `st`-Werte → `FIT` | `app/infrastructure/kickbase/dto.py` (`_to_status`) | `st=128` erscheint als fit mit 0.85 | P0-3 |
| D7 | Kaufpreis nur aus eigenem `trade_log` | `app/application/run_tick_uc.py` (`_load_buy_history`) | zugeloste/App-Käufe ohne Kaufpreis ⇒ PROFIT-Exits & Transfer-Erfolge nicht steuerbar | P1-6 |
| D8 | 25 History-Calls/Tick für Daten, die im Payload stehen | `app/application/player_enrichment.py:104` | Ban-Risiko; 12 von 22 Marktspielern trotzdem ohne Trend | P1-7 |
| D9 | `avg_points_last5` ist in Wahrheit der Saison-Ø | `app/application/player_enrichment.py:221` | Bankdrücker sieht aus wie im Oktober | P1-8 |
| D10 | Kaderlimit hartkodiert `15` | `app/domain/kb_rules.py:40` | real 11–25, Admin-Einstellung | P1-9 |
| D11 | Keine Aufstellungs-Aktion | Gateway/Executor | teuerste Regel (−100/Slot) ohne Ausführungspfad | P0-4 |
| D12 | Fester 120-min-Takt | `app/config.py:31`, `scheduler.py:60` | ~11 von 12 Ticks im Leerlauf; kann 20:35 statt 20:15 feuern | P1-10 |

### 4.1 Fehler im Master-Prompt (`docs/master_prompt.md`)
| § | Steht da | Korrekt |
|---|---|---|
| §1.1 | „max. 3 Spieler pro Club" | Liga-Einstellung 1–11 |
| §1.1 | „2 TW / 5 DEF / 5 MID / 3 STK-Kader" | **frei erfunden** — es gibt nur Kaderlimit + gültige Formation |
| §2 | `SELL_INSTANT` „meist unter Marktwert" | **voller MW**, sofort |
| §3 | — | Gebot < MW−10 % nicht absetzbar; Underpay-Block möglich |
| §3 | — | Kickbase bietet selbst ~MW auf dein Listing |
| §3 | Overbid-Deckel „+15 %" | willkürlich; Aufschlag folgt aus Trend×Restlaufzeit + `ofc` |
| §4 | nur `ticks_until_matchday_start` | zweite Uhr fehlt: **22:00 MW-Update** (`mvud`) |
| §1.2 | „Restspielplan, Gegnerstärke" gefordert | Daten existieren im JSON nicht |
| — | kein Wort zum Wertungsmodus | Saisonpunkte vs. H2H brauchen anderes Risikoprofil |
| §8 | `rules_last_verified: 2026-09-16` (`master_prompt_loader.py:33`) | bei jedem Prompt-Merge nachziehen |

### 4.2 Aktiver Code-Pfad (Orientierung)
```
Tick (alle 120 min)
 → get_league_me (Cash)  → get_squad  → get_market  → list_matchdays
 → PlayerEnricher: 1× GET /marketvalue/365 je Spieler (Kader + Top-10-Markt nach MW)
 → _build_user_payload (ai_decision_engine.py)
 → Master-Prompt als Cache-Prefix → claude-sonnet-4-6, Tool-Use „submit_decision"
 → genau 1 Aktion → TradeExecutor (dry_run-fähig) → trade_log → SMTP
```
**Toter Code** (im AI-Only-Modus nicht im Pfad): `app/application/heuristic_engine.py`,
`app/application/llm_curator.py`, `app/domain/scoring.py`, `app/domain/kb_rules.py`.
Nicht anfassen, nicht erweitern — ggf. in einem separaten Aufräum-Paket entfernen.

---

## 5. Testgerüst — 6 Stufen

| Stufe | Was | Wo | Wann |
|---|---|---|---|
| **1 Unit** | Reine Funktionen: DTO-Parsing, `_max_negative_allowed`, Trend-Mathe, Formationsvalidierung | `tests/domain`, `tests/application` | jeder Commit |
| **2 Contract** | Echte Payloads via VCR-Cassette → DTO. Bricht, wenn Kickbase ein Feld umbenennt | `tests/infrastructure/kickbase` | jeder Commit |
| **3 Payload-Snapshot** | Kompletter USER-JSON gegen `tests/application/snapshots/user_payload.json` | neu (P0-0.4) | jeder Commit |
| **4 Prompt-Eval** | Feste Szenarien → echter Modell-Call → Assertion auf `action`/`intent` | `tests/eval/`, Marker `@pytest.mark.eval` | vor jedem Prompt-Merge |
| **5 Shadow-Run** | `dry_run=true` gegen die echte Liga ≥ 7 Tage, `trade_log` auswerten | Produktiv-Container | vor dem Scharfschalten |
| **6 Armed** | `dry_run=false`, ein Paket nach dem anderen | Produktiv | zuletzt |

Marker registrieren (`pyproject.toml`, wegen `--strict-markers`):
```toml
[tool.pytest.ini_options]
markers = ["eval: kostet echte LLM-Calls, nicht im Default-Run"]
addopts = "-ra --strict-markers --strict-config --cov=app --cov-report=term-missing -m 'not eval'"
```

---

## 6. Die Pakete im Detail

### PHASE 0 — Discovery & Guardrails
> Reine Vorarbeit, kein Produktionsrisiko. Schaltet alles andere frei. Aufwand ~1 Tag.

#### P0-0.1 — Endpoint-Discovery erweitern
`scripts/inspect_endpoints.py` um folgende **read-only** Endpunkte ergänzen:
```
/v4/leagues/{l}/lineup
/v4/leagues/{l}/lineup/overview
/v4/leagues/{l}/lineup/selection
/v4/leagues/{l}/settings
/v4/leagues/{l}/ranking
/v4/leagues/{l}/players/{p}/performance
/v4/leagues/{l}/players/{p}
/v4/leagues/{l}/managers/{m}/transfer
/v4/leagues/{l}/squad
/v4/competitions/1/table
/v4/competitions/1/matchdays
```
⚠️ `/v4/bonus/collect` **nicht** aufnehmen — sammelt vermutlich tatsächlich ein (→ P2-15).
Zusätzlich `scripts/dump_keys.py`: gibt je Datei Key-Sets + Beispielwerte aus.
**DoD:** `tmp/inspect/*.json` für alle Endpunkte vorhanden, Key-Übersicht ausgegeben.

#### P0-0.2 — Die 3 offenen Fragen beantworten
Siehe §8. Ergebnisse **dort eintragen** und in `docs/api_notes.md` dokumentieren.
**DoD:** §8 enthält keine offene Frage mehr.

#### P0-0.3 — Cassettes neu aufnehmen
`scripts/record_cassettes.py` um die neuen Endpunkte erweitern, dann live aufnehmen.
**Wichtig:** eine Aufnahme **mit aktivem eigenen Listing und mindestens einem Gebot darauf** —
sonst fehlt genau der Fall für P0-2. Redaktion läuft über `tests/infrastructure/kickbase/vcr_config.py`.
**DoD:** neue Cassettes im Repo, `pytest` grün.

#### P0-0.4 — Payload-Snapshot-Test
Neu: `tests/application/test_user_payload_snapshot.py` + `tests/application/snapshots/user_payload.json`.
Baut aus den Cassette-Daten einen realistischen `DecisionContext` und vergleicht
`_build_user_payload(ctx)` gegen den Snapshot.

Dazu die **Gap-Assertions**, die heute rot sind und das DoD von Phase 1 definieren:
```python
def test_known_gaps_are_closed(payload):
    assert payload["budget"]["team_value"] > 0  # P0-1
    assert payload["budget"]["max_negative_allowed"] < 0  # P0-1
    assert all(p["start_probability_next"] is not None for p in payload["market"])  # P0-3
    assert all(p["avg_points_last5"] is not None for p in payload["market"])  # P0-3
```
Bis P0-1/P0-3 gemerged sind: `@pytest.mark.xfail(strict=True)`.
**DoD:** Snapshot-Test läuft, Gap-Test ist als xfail grün.

#### P0-0.5 — Contract-Drift-Checker
`scripts/check_contract.py`: läuft live, vergleicht Key-Sets gegen eine erwartete Liste,
meldet *fehlend* / *neu*. Kein CI-Job (braucht Credentials) — **monatlicher manueller Lauf**.
**DoD:** Skript läuft, Referenzliste in `docs/api_notes.md`.

---

### PHASE 1 — P0: Bot handlungsfähig machen
> Ohne diese 5 Pakete ist der Bot nicht wettbewerbsfähig. Aufwand gesamt ~3–4 Tage.

#### P0-1 — Team-Value & Markt-Metadaten  *(höchster Hebel, kleinster Aufwand)*
**Behebt:** D1
1. `MarketResponseDTO` um Root-Felder `tv, mvud, dt, day, nps, sn` erweitern.
2. Neues Domain-Objekt:
   ```python
   @dataclass(frozen=True, slots=True)
   class MarketSnapshot:
       players: tuple[MarketPlayer, ...]
       team_value: Decimal
       mv_update_at: datetime | None  # mvud
       next_matchday_start: datetime | None  # dt
       matchday: int
       squad_size: int
   ```
3. `KickbaseGateway.get_market()` → Rückgabetyp `MarketSnapshot`.
   **Migration:** `FakeKickbase` in `tests/application/conftest.py` + `run_tick_uc` anpassen.
4. `run_tick_uc`: `team_value` aus dem Snapshot; `next_matchday_start` primär aus `dt`,
   `list_matchdays()` nur noch als Fallback ⇒ **ein HTTP-Call weniger pro Tick**.
5. **`Squad.team_value` und `Squad.budget` ersatzlos entfernen** — die Default-0-Felder sind die
   Ursache von D1 und laden zur Wiederholung ein.
6. `mv_update_at` ins USER-JSON (`budget`-Block oder Top-Level `mv_update_at_iso`).

**Tests:** Unit `_max_negative_allowed` mit echtem TV · Contract-Test Root-Felder ·
Snapshot zeigt `team_value: 135562602`, `max_negative_allowed: -44735658`.
**DoD:** Gap-Assertions 1+2 grün (xfail entfernen).
**Loop-Stopp:** Falls `mvud` das *letzte* statt des *nächsten* Updates ist — prüfen, im Code
benennen, §3.1 korrigieren. Das entscheidet die Timing-Logik in P1-10.

#### P0-2 — Gebote parsen
**Behebt:** D2, D3 · **Blockiert durch:** P0-0.2 (Feldname), P0-0.3 (Cassette mit Gebot)
1. `MarketPlayerDTO` um das Offers-Array erweitern → `tuple[MarketOffer, ...]`;
   `dto.py:237` (`offers=()`) ersetzen.
2. `_open_bids_total` und `_incoming_offers` funktionieren danach **ohne weitere Änderung**.
3. Seller-ID-Erkennung (String **oder** Objekt) gegen den neuen Payload gegenprüfen.

**Tests:** Contract gegen Cassette mit echtem Gebot · Unit synthetisch
(eigenes Gebot auf fremdes Listing ⇒ `open_bids_total > 0`; fremdes Gebot auf eigenes Listing
⇒ `incoming_offers` gefüllt) · Snapshot zeigt `incoming_offers[0].offer_id`.
**DoD:** `ACCEPT_OFFER` ist im Shadow-Run mindestens einmal mit echter `offer_id` gewählt worden.
⚠️ Vorher **nicht scharf schalten** — Accept mit erfundener ID ist ein Fehlerpfad.

#### P0-3 — Marktspieler-Leistungsdaten + `prob`
**Behebt:** D4, D5, D6 · **Blockiert durch:** P0-0.2 (`prob`-Richtung)
1. `MarketPlayerDTO`: `p, ap, prob, ofc, isn, dt` übernehmen;
   `to_market_player()` setzt `average_points`/`total_points` statt `0.0`/`0`.
2. `MarketPlayer` um `start_probability_raw: int | None`, `offer_count: int`, `is_new: bool`,
   `listed_at: datetime | None`.
3. `player_enrichment.py`: `_START_PROBABILITY_BY_STATUS` wird **Fallback**; primär:
   ```python
   # Richtung erst nach P0-0.2 fixieren! 1 = sicher Startelf (zu bestätigen)
   _PROB_TO_PROBABILITY = {1: 0.95, 2: 0.80, 3: 0.55, 4: 0.30, 5: 0.05}
   ```
   `missing_data:start_probability_next_heuristic` nur noch ohne `prob` setzen.
4. `_market_entry` um `offer_count` erweitern (Grundlage P2-13).
5. `_to_status()`: Unbekanntes auf `UNKNOWN_2`/`questionable` mappen statt `FIT`, plus Log-Warnung.

**Tests:** Unit `prob`-Mapping inkl. Fallback · Snapshot: kein Marktspieler mehr mit
`avg_points_last5: null`, wenn `ap` vorhanden · Regression: `st=128` **nicht** „fit/0.85".
**DoD:** Gap-Assertions 3+4 grün.

#### P0-4 — Aufstellung setzen
**Behebt:** D11
**Designentscheidung:** Die −100-Regel wird **nicht** dem LLM überlassen. Zwei Mechanismen:
- **Deterministischer Startelf-Guard** (vor der LLM-Abfrage, jeden Tick): < 11 aufgestellt oder
  ein aufgestellter Spieler verletzt/gesperrt ⇒ deterministisch auffüllen, sortiert nach
  `prob` × erwarteten Punkten, in gültiger Formation.
- **LLM-Aktion `SET_LINEUP`** nur zum *Optimieren* einer bereits vollständigen Elf.

1. Gateway: `get_lineup(league_id)`, `set_lineup(league_id, formation, player_ids)`.
2. Client: `POST /v4/leagues/{l}/lineup` mit `{"type": "<formation>", "players": [...]}`.
3. Neues Domain-Modul `app/domain/lineup.py`:
   - erlaubte Formationen als Konstante
   - `validate_lineup(players, formation) -> list[str]` (Fehlerliste)
   - `best_lineup(squad, formation_candidates) -> Lineup` (greedy über `prob` × erwartete Punkte)
4. `TradeAction.SET_LINEUP` + Executor-Dispatch, **dry-run-sicher**.
5. **Harte Vorvalidierung im Executor** (nicht im Prompt): genau 11 IDs, alle im Kader,
   Formation gültig, Positionszählung passt. Ungültig ⇒ nicht absetzen, als Fehler loggen.
6. Kill-Switch: ENV `KB_LINEUP_WRITES_ENABLED` (Default `false`).

**Tests:** Unit `validate_lineup` (jede Formation, Unter-/Überbesetzung, Duplikate, Fremd-IDs) ·
Unit `best_lineup` mit konstruierten Kadern · Executor-Test: ungültiges LLM-Lineup geht **nicht** raus ·
Contract-Test auf die POST-Body-Form.
**DoD:** **Eine volle Woche `dry_run`**, geloggte Aufstellungen gegen die App verglichen.
Erste Write-Aktion, die direkt Punkte beeinflusst.

#### P0-5 — Master-Prompt korrigieren  *(zuletzt in Phase 1!)*
**Erst wenn P0-1…P0-4 gemerged sind.** Edits: siehe §4.1 (Tabelle enthält alle Stellen).
Zusätzlich:
- Aktion `SET_LINEUP` in §2 und §6 aufnehmen (Feld `lineup: {formation, player_ids[]}`)
- §4: `mv_update_at_iso` als zweite Zeitachse einführen
- `RULES_LAST_VERIFIED` in `app/application/master_prompt_loader.py:33` aufs Merge-Datum ziehen
- `temperature=0` im Decision-Call sicherstellen (`app/infrastructure/llm/anthropic_client.py`)

**Eval-Szenarien** (`tests/eval/scenarios/*.json`, je 3 Läufe):

| Szenario | Erwartete Aktion | Schwelle |
|---|---|---|
| Konto −2 Mio, 45 min bis Anpfiff, kein Gebot offen | `SELL_INSTANT` / `ACCEPT_OFFER` | 3/3 |
| 10 Startelf-Spieler, 3 h bis Anpfiff, 3 fitte auf der Bank | `SET_LINEUP` | 3/3 |
| Profit-Spieler +18 % in 7 d, 1-d-Trend −1,2 %, MW nahe `mv_max_30d` | `SELL_LIST`, `intent=PROFIT` | 2/3 |
| Kandidat mit `prob`=„spielt nicht", hoher Ø-Punkte-Wert | **kein** `BUY` | 3/3 |
| Konto +5 Mio, 4 Tage bis Anpfiff, nichts Auffälliges | `HOLD` | 2/3 |

**DoD:** Eval grün · 7 Tage Shadow ohne Executor-Fehler.

---

### PHASE 2 — P1: Von „funktioniert" auf „gut"
> Aufwand ~3 Tage. Ab hier wird der Bot rechenfähig statt nur regelkonform.

#### P1-6 — Kaufpreis & G/V aus Kickbase
**Behebt:** D7 · `prc`/`mvgl` → `SquadPlayer.buy_price`, `SquadPlayer.unrealized_pnl`.
`buy_history` aus dem `trade_log` bleibt **Intent-Quelle**, ist aber nicht mehr Preisquelle.
Fallback für fehlendes `prc`: `/managers/{m}/transfer` (`trp`).
**Test:** Konsistenz-Check über die Cassette: `mv - prc == mvgl`.
**DoD:** Jeder Kaderspieler im USER-JSON hat `bought_at_price` ≠ null.

#### P1-7 — Trends aus Payload statt 25 HTTP-Calls
**Behebt:** D8 · `tfhmvt` (24 h €) und `sdmvt` (7 d €) übernehmen, in Prozent umrechnen.
`get_market_value_history` nur noch für `mv_max_30d` + 30-d-Trend, und nur für **Kader + Shortlist**
(nicht Top-10-nach-MW).
**Test (validiert die Feldsemantik empirisch):** der aus `tfhmvt` abgeleitete Prozentwert muss
(±0,1 pp) dem aus der History berechneten entsprechen — analog `sdmvt` gegen den 7-d-Trend.
**DoD:** Requests/Tick im Log messbar gesunken; alle Marktspieler haben 24-h/7-d-Trends.

#### P1-8 — Echte Form & Minuten
**Behebt:** D9 · `/v4/leagues/{l}/players/{p}/performance` → `ph[].p`, `ph[].mp`, `ph[].day`.
Daraus: echtes `avg_points_last5`, `minutes_last5`, `starts_last5` (Rotationsrisiko).
`missing_data:avg_points_last5_using_season_avg` entfällt.
**Kostenkontrolle:** 1 Call/Spieler, nur Kader + Markt-Shortlist (Top-N nach `ap`, **nicht** nach MW),
mit **Tages-Cache in SQLite** — Spieltagspunkte ändern sich nur montags.
**Test:** Unit auf die Fenster-Mathematik bei < 5 Spieltagen Historie (muss `None` liefern, nicht verzerren).

#### P1-9 — Liga-Settings lesen
**Behebt:** D10 · `/leagues/{l}/settings` + `/me → tpc[]` ⇒ `constraints.squad_limit`,
`constraints.club_limit`, `constraints.underpay_blocked`, `constraints.scoring_mode`,
`constraints.players_per_club: {tid: n}`.
`kb_rules.py:40` (`_MAX_SQUAD_SIZE = 15`) wird Konfiguration statt Konstante.
**Test:** Contract auf die Settings-Response · Unit: BUY, der das Vereinslimit **inkl. offener Gebote**
sprengt, wird als Verstoß erkannt.
**Prompt-Nachzug:** §1.1 auf `constraints.*` umstellen (kleiner Prompt-Edit, eigener Commit).

#### P1-10 — Scheduler auf Ereignis-Fenster
**Behebt:** D12 · `KickbaseScheduler` kann bereits Cron (`set_digest`, `CronTrigger`,
Zeitzone `Europe/Berlin` ist gesetzt). Analog `set_windows()`:

| Fenster | Zweck |
|---|---|
| täglich 21:45 | Gebote platzieren / Listings prüfen vor dem MW-Update |
| täglich 22:15 | Gewinner/Verlierer auswerten, Positionen drehen |
| `next_matchday_start − 45 min` | Konto ins Plus, Startelf final |
| `next_matchday_start + 5 min` | Minus-Fenster öffnen |
| Mo 18:30 | Punkte/Prämien-Review |
| Intervall-Fallback (z. B. 180 min) | alles andere |

Englische Wochen: Fenster **relativ zu `next_matchday_start`** berechnen, nicht hart auf Freitag.
**Test:** Unit auf die Fenster-Berechnung mit Dienstag-18:30-Spieltag · Scheduler-Test, dass Fenster
und Intervall-Job sich nicht doppeln (`max_instances=1`, `coalesce=True` sind bereits gesetzt).

---

### PHASE 3 — P2: Top-Niveau
> Aufwand ~3–4 Tage. Pakete unabhängig, Reihenfolge frei.

| # | Paket | Kern | Test-Schwerpunkt |
|---|---|---|---|
| **P2-11** | Spielplan & Gegnerstärke | `/competitions/1/table` + `mdsum[]` ⇒ pro Spieler `next_opponent`, `is_home`, `opponent_rank`, `fdr` (1–5 aus Tabellenplatz + Tordifferenz). Cache 1×/Tag. Erst damit hat Prompt-§1.2 eine Datengrundlage. | Unit auf die FDR-Ableitung; Snapshot |
| **P2-12** | Ligakontext | `/leagues/{l}/ranking` ⇒ `my_rank`, `points_behind_leader`, `matchdays_left`, `rivals[]`. Bei H2H zusätzlich der Gegner der Woche. Prompt: Risikoappetit = f(Rückstand × Restspieltage). | Eval: gleicher Kader, einmal führend / einmal 3000 Punkte zurück ⇒ unterschiedliche Aktion |
| **P2-13** | Overbid-Kalibrierung | `offer_count` (aus P0-3) + erwarteter Trend bis `expires_at` ⇒ Formel statt „+15 %"-Konstante | Eval: hoher `ofc` ⇒ höheres Gebot |
| **P2-14** | Saisonphasen-Gewichtung | `matchdays_left` ins JSON; Prompt §1: Trading-Gewicht fällt linear gegen 0 ab ST 26 | Eval: identisches Szenario an ST 5 vs. ST 30 |
| **P2-15** | Täglicher Bonus | `GET /v4/bonus/collect` als **eigener Cron-Job**, nicht im Entscheidungs-Tick. Streak-Zähler im `trade_log`. ⚠️ vorher in `dry_run` verifizieren, dass der Call idempotent ist. | manueller Erstlauf + Kontostand-Vergleich |
| **P2-16** | Mehrere Aktionen pro Tick | Im Deadline-Fenster (< 2 h) darf das LLM eine Aktionsliste liefern (max. 3, sequenziell, Abbruch beim ersten Fehler). Schema-Änderung in `_INPUT_SCHEMA` + Executor-Schleife. Sonst ist „verkaufen + aufstellen + nachkaufen" am Freitagabend strukturell unmöglich. | Executor-Test auf Abbruchverhalten; Eval auf ein Deadline-Szenario |

---

## 7. Abhängigkeiten

```
Phase 0 (Discovery + Snapshot + Eval-Gerüst)
   │
   ├── P0-1 team_value/mvud ──┬── P1-10 Scheduler-Fenster
   ├── P0-2 Offers ───────────┤
   ├── P0-3 Marktdaten/prob ──┼── P2-13 Overbid
   ├── P0-4 Aufstellung ──────┤
   │                          │
   └──────────► P0-5 Prompt ◄─┘        (Prompt immer zuletzt im Block)
                   │
        ┌──────────┼──────────┬──────────┐
     P1-6 prc   P1-7 Trends  P1-8 Form  P1-9 Settings
                   │
        ┌──────────┼──────────┬──────────┬──────────┐
    P2-11 Spielplan  P2-12 Liga  P2-14 Phasen  P2-15 Bonus  P2-16 Multi-Action
```

**Kritischer Pfad:** Phase 0 → P0-1 → P0-3 → P0-5.
**Harte Blocker:** P0-2 und P0-3 brauchen P0-0.2. P0-5 braucht P0-1…P0-4.

---

## 8. Offene Fragen (Phase 0 beantwortet sie — Antworten hier eintragen!)

### F1 — Wie heißt das Offers-Array im Market-Payload?
**Status:** ❓ offen
**Verfahren:** Einen günstigen Spieler zum MW listen → 2–6 h warten, bis Kickbase selbst bietet →
`/v4/leagues/{l}/market` inspizieren. `ofc > 0` ist das Signal. Struktur des Eintrags festhalten
(Feldnamen für Offer-ID, Bieter-ID, Preis, Ablauf).
**Antwort:** _(hier eintragen)_
**Blockiert:** P0-2

### F2 — Ist `prob=1` die höchste oder niedrigste Startelf-Wahrscheinlichkeit?
**Status:** ❓ offen (Indiz: Cassette hatte `1` bei Guirassy/Baku, `5` bei einem 500k-Ersatzkeeper
⇒ vermutlich **1 = sicher Startelf**)
**Verfahren:** Gegen die App-Anzeige (5 Icons, Legende in der Aufstellungs-Ansicht) für
3–4 bekannte Spieler verifizieren. **Nicht raten** — ein invertiertes Mapping wäre schlimmer als keins.
**Antwort:** _(hier eintragen)_
**Blockiert:** P0-3

### F3 — Welche `st`-Werte existieren real?
**Status:** ❓ offen
**Verfahren:** Über alle Kader-/Markt-Dumps die vorkommenden `st`-Werte sammeln und den Spielern
in der App zuordnen. `PlayerStatus` kennt 0/1/2/4/8/16/32/64; in der API-Doku taucht `128` auf.
**Antwort:** _(hier eintragen)_
**Blockiert:** P0-3 (D6)

### F4 — Ist `mvud` der *nächste* oder der *letzte* MW-Update-Zeitpunkt?
**Status:** ❓ offen (Cassette: `2026-08-31T20:00:00Z`, aufgenommen am 31.08. ⇒ mehrdeutig)
**Verfahren:** Zwei Abrufe im Abstand von > 24 h vergleichen.
**Antwort:** _(hier eintragen)_
**Blockiert:** P1-10 (Fenster-Logik), beeinflusst P0-1

### F5 — Wie lange laufen *eigene* Listings maximal?
**Status:** ❓ offen (Kickbase-Listings empirisch 0,8–33 h; Community nennt für eigene Listings ~72 h)
**Verfahren:** Eigenes Listing anlegen, `exs` direkt danach ablesen.
**Antwort:** _(hier eintragen)_
**Blockiert:** nichts hart, aber relevant für die Prompt-Formulierung „Listing vs. Sofortverkauf"

---

## 9. Risiken & Kill-Switches

| Risiko | Gegenmaßnahme |
|---|---|
| `prob`-Richtung invertiert | F2 gegen die App verifizieren, nicht gegen Intuition. Mapping als benannte Konstante mit Kommentar. |
| Kickbase ändert Feldnamen | `scripts/check_contract.py` monatlich + Contract-Tests brechen laut statt still. Fehlendes Feld ⇒ `None` + `missing_data`-Flag, **nie Default-0** (das war D1). |
| Aufstellungs-Write zerstört eine gute Elf | Vorvalidierung im Executor (nicht im Prompt) · 7 Tage Shadow · ENV `KB_LINEUP_WRITES_ENABLED=false` |
| Rate-Limit / Ban | P1-7 senkt die Call-Zahl netto. P1-8/P2-11 nur mit Tages-Cache. `AsyncRateLimiter` bleibt harter Deckel. |
| Prompt-Regression durch Sampling | Eval mit 3 Läufen/Szenario vor jedem Prompt-Merge · `temperature=0` |
| Halbfertiger Zustand in Produktion | Ein Paket = ein Merge = ein Deploy. `dry_run=true` bleibt Default, bis Phase 1 komplett durch den Shadow ist. |
| Rechtlich/ToS | unverändert: Privatnutzung, konservatives Rate-Limit (siehe `PROJEKT.md` §1.3/§1.4) |

---

## 10. Änderungs- & Entscheidungslog
> Jede Session trägt hier eine Zeile ein. Format: `Datum · Paket · Was · Erkenntnis`

| Datum | Paket | Was | Erkenntnis / Entscheidung |
|---|---|---|---|
| 2026-09-23 | — | Plan erstellt | Recherche + Code-Audit abgeschlossen; 12 Defekte (§4), 5 offene Fragen (§8) |
| 2026-09-23 | P0-0.1 | Discovery auf 16 Endpunkte erweitert, `scripts/dump_keys.py` neu | `/v4/leagues/{l}/settings` **existiert nicht** (HTTP 500 `NotFound`) — die Liga-Settings stehen in `/me` + `/leagues/{l}/squad`. GET auf `/market/{pid}/offers` → 405 (nur POST), GET `/market/{pid}` → 405 (nur DELETE) ⇒ das Gebots-Array kann nur im `/market`-Payload stecken. |

---

## 11. Quellen (falls etwas nachgeschlagen werden muss)

**Offizielle Regeln:**
[Marktwert](https://help.kickbase.com/help/kickbase-marktwert) ·
[Punkteberechnung](https://help.kickbase.com/help/wie-werden-die-punkte-berechnet) ·
[Punktetabelle vollständig](https://us.kickbase.com/en-us/points-table) ·
[Kontostand & Aufstellung](https://help.kickbase.com/help/regel-4-kontostand-aufstellung) ·
[Konto im Minus](https://help.kickbase.com/help/kontoimminus) ·
[33 %-Regel](https://help.kickbase.com/help/wie-weit-darf-ich-ins-minus) ·
[Aufstellung](https://help.kickbase.com/help/alles-rund-um-deine-aufstellung) ·
[Gebot abgelehnt](https://help.kickbase.com/help/ich-habe-auf-einen-spieler-geboten-warum-habe-ich-ihn-nicht-zum-transferzeitpunkt-bekommen) ·
[Transfermarkt](https://help.kickbase.com/help/kickbase-transfermarkt) ·
[Classic Modus](https://help.kickbase.com/help/kickbase-seasonal) ·
[Neuerungen 26/27](https://help.kickbase.com/help/neuefeatures26) ·
[Admin-Settings](https://help.kickbase.com/help/admin-settings) ·
[Kaderbegrenzung](https://help.kickbase.com/en/help/kaderbegrenzung) ·
[Startelf-Wahrscheinlichkeiten](https://help.kickbase.com/help/startelfwahrscheinlichkeiten) ·
[Erfolge](https://help.kickbase.com/help/welche-erfolge-kann-ich-in-der-app-erhalten)

**API & Community:**
[kevinskyba/kickbase-api-doc](https://github.com/kevinskyba/kickbase-api-doc) (147 Endpunkte, Stand 03/2026) ·
[Ligabase Trading-Guide](https://ligabase.de/blog/kickbase-trading-guide) ·
[GIGA: unter Marktwert bieten](https://www.giga.de/tipp/kickbase-unter-marktwert-bieten-das-sollte-man-beachten/) ·
[Kickbest (xPts/FDR/Rohpunkte)](https://kickbest.app/) ·
[Fußball-Orakel: 8 Tipps](https://fussball-orakel.de/tipps.php)
