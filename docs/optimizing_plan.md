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
- [x] **P0-0.2** Die 5 offenen Fragen beantworten (-> §8) — F4/F5 geklärt, F1–F3 eingegrenzt
- [x] **P0-0.3** Cassettes neu aufnehmen — 15 Cassettes, **aktives eigenes Listing dabei**, Gebots-Fall fehlt noch (braucht ein echtes Gebot, §8/F1)
- [x] **P0-0.4** Payload-Snapshot-Test bauen — Snapshot + 4 einzeln messbare Gap-Tests
- [x] **P0-0.5** Contract-Drift-Checker (`scripts/check_contract.py`) + Baseline über 14 Endpunkte

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
| **0** | `pytest` grün · Snapshot-Datei existiert · alle 5 Fragen in §8 beantwortet **oder** mit belegtem Rest-Verfahren + entkoppeltem Folgepaket abgeschlossen (siehe §8.0) |
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
| `mvud` | **nächster** MW-Update-Zeitpunkt (20:00 UTC = 22:00 Berlin) — F4 beantwortet | ignoriert (P0-1 / P1-10) |
| `dt` | **Start des nächsten Spieltags** | ignoriert; stattdessen extra `list_matchdays()`-Call (P0-1) |
| `day` | Spieltagsnummer | ignoriert (P2-14: `matchdays_left`) |
| `nps` | Anzahl Spieler im eigenen Kader | ignoriert |
| `sn` | Saison | ignoriert |

### 3.2 `GET /v4/leagues/{l}/market` — Item-Felder
Verifizierte Keys: `i, fn, n, tid, pos, st, mv, mvt, p, ap, ofc, exs, prc, isn, iposl, dt, pim, prob`

| Feld | Bedeutung | Bot heute |
|---|---|---|
| `prob` | **Startelf-Wahrscheinlichkeit, 5 Stufen**, `1` = sicherste Startelf (Median-MW fällt monoton von 25,4 Mio bei `1` auf 3,6 Mio bei `5`). ⚠️ **Nur in der Spieltagswoche vorhanden** — am 23.09. (16 Tage bis Anpfiff) in 0 von 20 Items. | ignoriert; stattdessen pauschal 0.85 aus Verletzungsstatus (P0-3) |
| `p` / `ap` | Gesamt-/Ø-Punkte (in 14 von 22 Items vorhanden) | **hart auf 0 gesetzt** (P0-3) |
| `ofc` | **Anzahl Gebote** auf dieses Listing | ignoriert (P0-3 / P2-13) |
| `exs` | Sekunden bis Listing-Ablauf — **nur bei fremden/Kickbase-Listings**; eigene Listings tragen es nicht (F5) | genutzt ✅ |
| `prc` | Listing-Preis (bei Kickbase-Listings == `mv`) | genutzt ✅ |
| `mvt` | MW-Trendrichtung (0/1/2) | ignoriert |
| `isn` | „ist neu auf dem Markt" | ignoriert |
| `u` | Seller — String **oder** Objekt `{i, n, …}`; fehlt bei Kickbase-Listings | genutzt ✅ |
| **Offers-Array** | Feldname **unbekannt**; eingegrenzt: GET `/market/{p}/offers` -> 405 (nur POST), GET `/market/{p}` -> 405 (nur DELETE) ⇒ kann nur im `/market`-Payload stehen, sichtbar ab `ofc > 0` (F1). `/leagues/{l}/squad` liefert `ofc` je eigenem Spieler als billigeren Trigger. | **braucht ein echtes Gebot**, dann P0-2 |

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
| `GET/POST /v4/leagues/{l}/lineup` | Aufstellung lesen **und setzen**. POST-Body: `{"type":"4-4-2","players":["1235", …]}` . `/lineup/overview` liefert die Formation als `t` (z. B. `"3-5-2"`), die Deadline als `lis`, `lpc` = Zahl aufgestellter Spieler | P0-4 |
| ~~`GET /v4/leagues/{l}/settings`~~ | **existiert nicht** (HTTP 500 `NotFound`). Ersatz: `/me` liefert `isp`, `gpm`, `lnm`, `mgm`, `mgc`, `mppu`; `/leagues/{l}/squad` liefert `mppu` | P1-9 |
| `GET /v4/leagues/{l}/me` | `b` (Cash), `tpc[]` = **Spieler je Verein** (`{tid, npt}`), `mppu` = **Kaderlimit** (hier 16), `gpm`/`isp` = Modus-Flags | P1-9 |
| `GET /v4/leagues/{l}/players/{p}/performance` | `it[].ph[]` mit `day, p` (Punkte), `mp` (**Minuten**), `md`, `t1/t2`, `st` | P1-8 |
| `GET /v4/leagues/{l}/players/{p}` | `sl` (Startelf-Prognose als **bool**, Quelle `plpt`=„Ligainsider"), `mdsum[]` (**kommende Spiele**), `g`, `a`, `y`, `r`, `sec` | P0-3 / P2-11 |
| `GET /v4/leagues/{l}/ranking` | `us[]` mit `sp` (Saisonpunkte), `mdp`, `spl` (Platz), `tv`, **`lp[]` = Aufstellungen der Rivalen** | P2-12 |
| `GET /v4/competitions/1/table` | `tid, tn, cp, cpl, mc, gd, mdp, sp` → Gegnerstärke/FDR | P2-11 |
| `GET /v4/leagues/{l}/managers/{m}/transfer` | Transferhistorie: `pi, tty, trp` (Preis), `dt`, `othnm` | P1-6 (Fallback) |
| `GET /v4/leagues/{l}/squad` | zweite Kader-Sicht mit **`ofc` je eigenem Spieler** (= eingegangene Gebote) + `mppu` | P0-2 (Trigger) / P1-9 |
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

#### P0-0.2 — Die 5 offenen Fragen beantworten
Siehe §8. Ergebnisse **dort eintragen** und in `docs/api_notes.md` dokumentieren.
Auswertung läuft über `scripts/answer_open_questions.py` — wertet F1–F5 gegen
`tmp/inspect/*.json` + Cassetten aus und schreibt das `mvud`-Verlaufsprotokoll.
**DoD:** §8 enthält keine Frage mehr ohne Antwort **oder** ohne die drei Punkte aus §8.0
(ausgeschlossene Möglichkeiten belegt · Rest-Verfahren automatisiert · abhängiges Paket entkoppelt).

#### P0-0.3 — Cassettes neu aufnehmen
`scripts/record_cassettes.py` um die neuen Endpunkte erweitern, dann live aufnehmen.
**Wichtig:** eine Aufnahme **mit aktivem eigenen Listing und mindestens einem Gebot darauf** —
sonst fehlt genau der Fall für P0-2. Redaktion läuft über `tests/infrastructure/kickbase/vcr_config.py`.
**DoD:** neue Cassettes im Repo, `pytest` grün.

> **[Plan-Ergänzung 2026-09-23] Die Redaktion hätte personenbezogene Daten durchgelassen.**
> `_looks_like_user_context()` suchte nach `id` — Kickbase schreibt aber `i`. Damit wären mit den
> neuen Endpunkten Klarnamen, Profilbild-URLs und **User-IDs fremder Mitspieler** aus `/ranking`
> (`us[]`), dem `u`-Objekt im Market-Payload und `othnm` aus `/transfer` unredigiert ins Repo
> gewandert. Ohne diese Korrektur hätte das Paket sein eigenes Versprechen („Redaktion läuft über
> vcr_config.py") gebrochen.
> **Umgesetzt:** Manager-Objekte werden über `uim`/`unm`/`profile`/`em` erkannt, Spieler-Objekte
> über `pos`/`mv`/`pi`/`pn` — Spieler-Marker gewinnen, damit Spielernamen (öffentlich, von
> Contract-Tests geprüft) erhalten bleiben. Fremde Manager-IDs werden deterministisch
> pseudonymisiert, die eigene ID bleibt `FAKE_USER_ID`.
> **Abgesichert durch** `tests/infrastructure/kickbase/test_cassette_privacy.py` — prüft die
> Redaktionsfunktion gegen synthetische Payloads *und* jede eingecheckte Cassette.
>
> **Zweite Ergänzung:** die Contract-Tests hingen an Stückzahlen (`== 9` Spieler, `== 22`
> Angebote, `budget > 0`). Diese Zahlen ändern sich mit jedem Transfer und jeder Stunde am Markt
> — sie hätten bei jeder Neuaufnahme rot geleuchtet, ohne je einen Feldnamen-Drift zu zeigen.
> Jetzt prüfen sie Struktur (Feld vorhanden, Typ, Wertebereich). Die echte Drift-Erkennung
> leistet P0-0.5.

**Aufgenommen 2026-09-23:** `login`, `leagues_selection`, `league_me`, `squad`, `market`,
`matchdays`, `market_value` (über den Client) + `lineup`, `lineup_overview`, `league_squad`,
`ranking`, `manager_transfer`, `competition_table`, `player_detail`, `player_performance`
(roh, für P0-4/P1-8/P1-9/P2-11/P2-12).
**Offen:** `market.yaml` mit `ofc > 0`. Das Skript meldet am Ende, ob der Fall dabei war.

#### P0-0.4 — Payload-Snapshot-Test
Neu: `tests/application/test_user_payload_snapshot.py` + `tests/application/snapshots/user_payload.json`.
Baut aus den Cassette-Daten einen realistischen `DecisionContext` und vergleicht
`_build_user_payload(ctx)` gegen den Snapshot.

Dazu die **Gap-Assertions**, die heute rot sind und das DoD von Phase 1 definieren:
```python
assert payload["budget"]["team_value"] > 0  # P0-1
assert payload["budget"]["max_negative_allowed"] < 0  # P0-1
# P0-3: siehe Ergänzung unten - `is not None` misst den Defekt nicht
assert all(p["avg_points_last5"] is not None for p in payload["market"])  # P0-3
```
**DoD:** Snapshot-Test läuft, Gap-Tests sind als xfail grün.
Snapshot neu schreiben: `UPDATE_SNAPSHOTS=1 pytest tests/application/test_user_payload_snapshot.py`.

> **[Plan-Ergänzung 2026-09-23] Drei Dinge hätten das Paket wirkungslos gemacht.**
>
> **1. Der Payload war nicht reproduzierbar.** `MarketPlayerDTO.to_market_player()` rechnet `exs`
> (Sekunden bis Ablauf) gegen `datetime.now(UTC)` in eine absolute Zeit um, `_offer_entry()`
> greift ebenfalls auf die Wall-Clock zu. Ein Snapshot gegen eine JSON-Datei wäre bei **jedem
> Lauf** rot gewesen. Umgesetzt: fixes `NOW` im Test, `expires_at` wird aus dem Roh-`exs`
> nachgereicht, `expires_in_min` wird gerundet. Zusätzlich wacht
> `test_payload_is_deterministic` darüber, dass niemand neue `datetime.now()`-Aufrufe in den
> Builder einbaut. → **Für P0-1 vormerken:** die Zeitquelle gehört in den Context, nicht in die
> DTO-Schicht.
>
> **2. Gap-Assertion 3 war schon vor dem Paket grün.** `start_probability_next` ist **nie**
> `None` — `PlayerEnricher` setzt für jeden Spieler einen Wert aus dem Verletzungsstatus.
> `assert all(... is not None)` hätte D5 also nie gemessen, und das Phase-1-DoD
> („die 4 Gap-Assertions sind grün") wäre zu einem Viertel bedeutungslos gewesen. Der Defekt ist
> nicht „fehlt", sondern „alle bekommen denselben Wert": im Snapshot haben alle 21 Marktspieler
> genau 3 verschiedene Werte (0.15 / 0.55 / 0.85), und die sind 1:1 der Verletzungsstatus.
> Ersetzt durch eine Assertion auf die **Streuung innerhalb der fitten Spieler** plus das
> Verschwinden des `missing_data:start_probability_next_heuristic`-Flags. Die
> `is not None`-Prüfung bleibt als normaler Invarianten-Test erhalten.
>
> **3. Ein gebündelter xfail-Test hätte den Merge von P0-1 verschluckt.** Mit allen vier
> Assertions in einer Funktion bleibt `xfail(strict=True)` „grün", solange *irgendeine* fällt —
> P0-1 könnte fertig sein, ohne dass der Test es meldet. Jetzt hat jede Lücke einen eigenen
> Test; `strict` schlägt beim richtigen Merge mit XPASS an und erzwingt, den Marker zu entfernen.

**Snapshot-Stand 2026-09-23 (das sieht das LLM heute):** 8 Kaderspieler, 21 Marktspieler,
`team_value: 0`, `max_negative_allowed: 0`, `avg_points_last5` bei 20 von 21 Marktspielern `null`,
`start_probability_next` mit 3 distinkten Werten für 21 Spieler.

#### P0-0.5 — Contract-Drift-Checker
`scripts/check_contract.py`: vergleicht die Key-Profile gegen eine erwartete Liste,
meldet *fehlend* / *neu* / *schwächer*. Kein CI-Job (braucht Credentials) — **monatlicher
manueller Lauf**. Baseline: `docs/contract_baseline.json`, Anleitung in `docs/api_notes.md` §5.
**DoD:** Skript läuft, Referenz dokumentiert.

> **[Plan-Ergänzung 2026-09-23] Zwei Dinge fehlten, ohne die der Checker nicht in Betrieb geht.**
>
> **1. Henne-Ei bei der Referenzliste.** Der Plan setzt eine „erwartete Liste" voraus, sagt aber
> nicht, woher sie kommt. Von Hand gepflegt wäre sie bei 14 Endpunkten und ~400 Pfaden sofort
> veraltet. Umgesetzt: `--update` erzeugt bzw. aktualisiert die Baseline aus den letzten Payloads.
>
> **2. Ein Checker ohne Ausnahmeliste wird ignoriert.** `market.it[].prob` ist außerhalb der
> Spieltagswoche gar nicht da (§8/F2) — ein Checker ohne `optional`-Liste meldet das monatlich
> als Drift. Nach dem zweiten grundlos roten Lauf schaut niemand mehr hin, und ein echter Drift
> geht unter. Jeder Endpunkt hat deshalb `always` / `sometimes` / `optional`, wobei `optional`
> eine **Begründung pro Pfad** verlangt und bei `--update` erhalten bleibt.
>
> **Zugabe:** `--source cassettes` prüft denselben Vertrag gegen die eingecheckten Aufnahmen —
> läuft ohne Credentials und fängt Drift schon beim Neuaufnehmen der Cassettes.
>
> **Verifiziert:** Umbenennung `market.it[].mv` → `marketValue` simuliert ⇒ „✗ FEHLT it[].mv",
> „+ NEU it[].marketValue", Exit-Code 1.

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

> **[Plan-Ergänzung 2026-09-23]** Der Feldname bleibt unbekannt, bis ein echtes Gebot eingeht
> (§8/F1). Damit das Paket nicht wartet: Schritt 0 vorziehen — `ofc` aus dem Market-Item **und**
> aus `GET /v4/leagues/{l}/squad` übernehmen und als `offer_count` ins USER-JSON schreiben.
> Der Bot weiß damit *dass* Gebote vorliegen, bevor er weiß, *wie sie heißen*; der Prompt kann
> darauf schon reagieren (Listing halten statt Sofortverkauf). `ACCEPT_OFFER`/`DECLINE_OFFER`
> bleiben gesperrt, solange keine echte `offer_id` im Payload steht.

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
**Behebt:** D4, D5, D6 · **Blockiert durch:** nichts mehr (siehe Ergänzung)

> **[Plan-Ergänzung 2026-09-23]** Zwei Befunde aus Phase 0 ändern dieses Paket:
> 1. **`prob` fehlt außerhalb der Spieltagswoche komplett** (0 von 20 Items am 23.09.,
>    22 von 22 am 31.08.). Ein Mapping allein auf `prob` liefert 11 von 14 Tagen `None`.
>    ⇒ Quellen-Kette statt Einzelquelle: `prob` (wenn da) → `sl` aus
>    `GET /v4/leagues/{l}/players/{p}` (bool, ganzjährig) → Status-Heuristik → `None`
>    + `missing_data`-Flag. Welche Stufe gegriffen hat, gehört als `start_probability_source`
>    ins USER-JSON — sonst kann das LLM die Verlässlichkeit nicht gewichten.
> 2. **F3 ist durch Sammeln nicht abschließbar.** `_to_status()` darf Unbekanntes nicht auf
>    `questionable` umbiegen (das ist auch geraten), sondern braucht einen echten
>    `UNKNOWN`-Zustand + `missing_data`-Flag. Schritt 5 unten entsprechend lesen.
1. `MarketPlayerDTO`: `p, ap, prob, ofc, isn, dt` übernehmen;
   `to_market_player()` setzt `average_points`/`total_points` statt `0.0`/`0`.
2. `MarketPlayer` um `start_probability_raw: int | None`, `offer_count: int`, `is_new: bool`,
   `listed_at: datetime | None`.
3. `player_enrichment.py`: `_START_PROBABILITY_BY_STATUS` wird **Fallback**; primär:
   ```python
   # F2 beantwortet: 1 = sicherste Startelf (Median-MW fällt monoton 25,4 Mio -> 3,6 Mio).
   _PROB_TO_PROBABILITY = {1: 0.95, 2: 0.80, 3: 0.55, 4: 0.30, 5: 0.05}
   ```
   `missing_data:start_probability_next_heuristic` nur noch ohne `prob` setzen.
4. `_market_entry` um `offer_count` erweitern (Grundlage P2-13).
5. `_to_status()`: Unbekanntes auf einen expliziten `UNKNOWN`-Zustand mappen (nicht `FIT`, auch nicht `questionable`), `injury_status: "unknown"` + `missing_data`-Flag, plus Log-Warnung.

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
**Harte Blocker:** P0-2 braucht ein echtes Gebot im Payload (§8/F1) — der `ofc`-Teil ist davon entkoppelt. P0-3 ist seit der Ergänzung in §6 nicht mehr blockiert. P0-5 braucht P0-1…P0-4.

---

## 8. Offene Fragen — Stand nach Phase 0

> Auswertung reproduzierbar: `python -m scripts.answer_open_questions`.
> Vollständige Belege in `docs/api_notes.md` §4.

### 8.0 Ergänzung 2026-09-23 — wie Phase 0 trotz offener Fragen abschließt

> **[Plan-Ergänzung]** Der ursprüngliche Plan sprach an drei Stellen von „3 offenen Fragen",
> §8 listet aber fünf (F1–F5). F4 blockiert P1-10 und beeinflusst P0-1 — es wäre still
> durchgerutscht. Korrigiert auf 5.
>
> Zweitens: **F1, F2 und F5 sind nicht durch Datenlesen beantwortbar.** Sie verlangen eine
> Aktion im Spiel (Spieler listen, auf ein fremdes Gebot warten) bzw. einen Blick in die App —
> beides kann kein Skript erledigen. Nach dem ursprünglichen DoD („alle Fragen beantwortet")
> bliebe Phase 0 dauerhaft offen und würde P0-2 und P0-3 mitblockieren, also die halbe Phase 1.
>
> **Regel ab jetzt:** eine Frage gilt als *abgeschlossen*, wenn entweder die Antwort feststeht
> **oder** (a) belegt ist, welche Möglichkeiten ausgeschlossen sind, (b) das Rest-Verfahren
> benannt und automatisiert ist, und (c) das abhängige Paket so umgebaut ist, dass es ohne die
> Antwort nicht falsch entscheidet. Punkt (c) ist die eigentliche Absicherung — siehe die
> Entkopplungs-Hinweise bei P0-2 und P0-3.

**Was noch dich braucht (nicht automatisierbar):**

| # | Deine Aktion | Danach | Schaltet frei |
|---|---|---|---|
| F1 | Einen eigenen Spieler listen und warten, bis in der App ein Gebot eingeht (`ofc > 0`) | `python -m scripts.inspect_endpoints` → schreibt `tmp/inspect/offers_found.json` | P0-2 |
| F2 | In der App die Aufstellungsansicht öffnen, die 5 Startelf-Icons mit `prob` von 3–4 bekannten Spielern vergleichen | Ergebnis in §8/F2 eintragen | endgültige Bestätigung für P0-3 |
| F5 | Dasselbe Listing nach > 72 h erneut ansehen: noch da? | `python -m scripts.answer_open_questions` | nur Prompt-Formulierung |

---

### F1 — Wie heißt das Offers-Array im Market-Payload?
**Status:** 🟡 eingegrenzt, Antwort braucht ein echtes Gebot
**Belegt:** `GET /v4/leagues/{l}/market/{p}/offers` → **405**, `Allow: POST`.
`GET /v4/leagues/{l}/market/{p}` → **405**, `Allow: DELETE`. Es gibt also keinen Lese-Endpunkt
für Gebote; das Array kann nur im `/market`-Payload selbst liegen und erscheint erst bei
`ofc > 0`. Zusätzlich liefert `GET /v4/leagues/{l}/squad` ein `ofc` **je eigenem Spieler** —
der billigere Trigger, um überhaupt zu erkennen, dass ein Gebot vorliegt.
**Rest-Verfahren:** automatisiert — `scripts/inspect_endpoints.py` schreibt jeden Market-Eintrag
mit `ofc > 0` nach `tmp/inspect/offers_found.json` und listet dessen Keys.
**Blockiert:** P0-2

### F2 — Ist `prob=1` die höchste oder niedrigste Startelf-Wahrscheinlichkeit?
**Status:** 🟢 beantwortet (starke Evidenz), App-Gegenprobe offen
**Antwort:** **`1` = sicherste Startelf.** Median-Marktwert je Stufe (Cassette 31.08., n=22):
`1` → 25,4 Mio (n=6) · `2` → 12,2 Mio (n=2) · `3` → 13,3 Mio (n=2) · `4` → 10,3 Mio (n=3) ·
`5` → 3,6 Mio (n=9). Stufe 1 enthält Guirassy/Baku/Maza, Stufe 5 den 500k-Ersatzkeeper sowie
alle Spieler mit `st != 0`. Die Delle bei 2/3 ist bei n=2 bedeutungslos.
**⚠️ Wichtiger Nebenbefund:** im Lauf vom 23.09. fehlt `prob` in **allen 20** Market-Items.
Unterschied: 31.08. = 4 Tage bis Anpfiff, 23.09. = 16 Tage (Länderspielpause).
**Arbeitshypothese:** `prob` existiert nur in der Spieltagswoche.
→ **Konsequenz für P0-3:** `prob` darf nicht die einzige Quelle sein. `sl` (bool) aus
`GET /v4/leagues/{l}/players/{p}` ist ganzjährig verfügbar und wird Primärquelle; `prob` bleibt
der billige Massen-Indikator, wenn vorhanden. Fehlen beide → `None` + `missing_data`-Flag,
niemals ein erfundener Default.
**Blockiert:** nichts mehr (P0-3 ist über die Fallback-Kette entkoppelt)

### F3 — Welche `st`-Werte existieren real?
**Status:** 🟡 Stichprobe zu klein für eine abschließende Enum-Aussage — Paket entkoppelt
**Antwort:** real beobachtet wurden nur **0** (69×), **2** (4×) und **4** (2×), über Live-Dumps
und Cassette. `128` kam **nicht** vor. Die Stichprobe (8 eigene Spieler, 42 Marktspieler, eine
Liga, zwei Zeitpunkte) trägt keine Vollständigkeitsaussage. `stl` war überall leer.
**Konsequenz für P0-3 (D6):** die Frage ist durch Sammeln nicht abschließbar. Statt auf eine
vollständige Enum-Liste zu warten, muss `_to_status()` unbekannte Werte auf einen expliziten
`UNKNOWN`-Zustand + `missing_data`-Flag abbilden — **nie auf `FIT`**. Damit ist das Paket
unabhängig von der Antwort korrekt.
**Blockiert:** nichts mehr

### F4 — Ist `mvud` der *nächste* oder der *letzte* MW-Update-Zeitpunkt?
**Status:** 🟢 beantwortet
**Antwort:** **der nächste.** Zwei Beobachtungen, beide mit `mvud` in der Zukunft:
31.08. 11:01 Z → `mvud` 31.08. 20:00 Z (+9,0 h) · 23.09. 16:09 Z → `mvud` 23.09. 20:00 Z (+3,9 h).
Wäre es der letzte Zeitpunkt, müsste er am 23.09. auf den 22.09. zeigen. 20:00 UTC = 22:00 Berlin
deckt sich mit der dokumentierten Update-Zeit. Protokoll wächst in `tmp/mvud_log.json`.
**Rest-Unsicherheit:** welchen Wert `mvud` zwischen 20:00 und 24:00 UTC zeigt, ist ungeprüft —
für P1-10 irrelevant, solange der Scheduler `mvud` als Ziel nimmt und nach Ablauf neu lädt.
**Blockiert:** nichts mehr

### F5 — Wie lange laufen *eigene* Listings maximal?
**Status:** 🟢 im Kern beantwortet
**Antwort:** **Eigene Listings tragen kein `exs`.** Fremd-/Kickbase-Listings: 19 von 19 mit
`exs`, Spanne **0,3 h – 43,7 h** (§2.3 nannte 0,8–33 h — Spanne ist also weiter). Das eigene
Listing (Spieler 1809, gelistet 23.09. 13:31 Z, `prc` 9,2 Mio bei `mv` 8,81 Mio) hat nur `dt`
= Listing-Zeitpunkt. **Deutung:** es läuft nicht automatisch ab, sondern blockiert den
Kaderplatz, bis es angenommen oder per `DELETE /market/{p}` zurückgezogen wird.
**Für den Prompt (P0-5):** Listing = Plan A ohne Zeitdruck, aber ohne Zuschlagsgarantie;
Sofortverkauf = garantierter Plan B, wenn das Konto bis zum Anpfiff ins Plus muss.
**Blockiert:** nichts

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
| 2026-09-23 | P0-0.2 | F1-F5 gegen echte Payloads ausgewertet, `scripts/answer_open_questions.py` + `docs/api_notes.md` neu | **F4 beantwortet:** `mvud` ist der *nächste* Update-Zeitpunkt. **F5:** eigene Listings tragen kein `exs` - sie laufen nicht ab. **F2:** `prob=1` = sicherste Startelf, aber `prob` fehlt außerhalb der Spieltagswoche komplett -> P0-3 braucht eine Quellen-Kette. **F3:** nur st 0/2/4 real gesehen, kein 128 - nicht abschließbar, D6 unabhängig lösen. **F1:** kein Lese-Endpunkt für Gebote (405), braucht ein echtes Gebot. |
| 2026-09-23 | P0-0.3 | 15 Cassettes neu aufgenommen, Redaktion gehärtet, Contract-Tests entzahlt | Die alte Redaktion erkannte Manager-Objekte nicht (`id` statt `i`) - Klarnamen und IDs fremder Mitspieler wären ins Repo gewandert. Neuer Privacy-Test sichert das ab. Cassette enthält ein aktives eigenes Listing, aber noch kein Gebot. |
| 2026-09-23 | P0-0.4 | Snapshot-Test + 4 einzelne Gap-Tests | Der Payload war wegen `datetime.now()` in der DTO-Schicht nicht reproduzierbar - ohne Fix waere der Snapshot bei jedem Lauf rot. Gap-Assertion 3 (`start_probability_next is not None`) war bereits gruen und haette D5 nie gemessen; ersetzt durch eine Streuungs-Assertion. |
| 2026-09-23 | P0-0.5 | Contract-Checker + Baseline ueber 14 Endpunkte | Baseline muss erzeugbar sein (`--update`), sonst Henne-Ei. `optional`-Ausnahmen mit Begruendung noetig, sonst meldet der Checker das saisonale `prob` monatlich als Drift und wird ignoriert. |

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
