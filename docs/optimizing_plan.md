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

### Phase 0 — Discovery & Guardrails · Status: **abgeschlossen** (2026-09-23)
- [x] **P0-0.1** Endpoint-Discovery erweitern (`scripts/inspect_endpoints.py`)
- [x] **P0-0.2** Die 5 offenen Fragen beantworten (-> §8) — F4/F5 geklärt, F1–F3 eingegrenzt
- [x] **P0-0.3** Cassettes neu aufnehmen — 15 Cassettes, **aktives eigenes Listing dabei**, Gebots-Fall fehlt noch (braucht ein echtes Gebot, §8/F1)
- [x] **P0-0.4** Payload-Snapshot-Test bauen — Snapshot + 4 einzeln messbare Gap-Tests
- [x] **P0-0.5** Contract-Drift-Checker (`scripts/check_contract.py`) + Baseline über 14 Endpunkte
- [x] **P0-0.6** Eval-Gerüst + `eval`-Marker  ⟵ *[Plan-Ergänzung, siehe §6]*
- [x] **P0-0.7** Verifikation gegen echte Calls + Korrekturen  ⟵ *[Plan-Ergänzung, siehe §6]*

### Phase 1 — P0: Bot handlungsfähig machen · Status: **Code abgeschlossen** (2026-09-23), Shadow-Lauf offen
- [x] **P0-1** Team-Value & Markt-Metadaten (`tv`, `mvud`, `dt`)
- [x] **P0-2** Gebote parsen (Offers-Array) — `ofc`-Teil umgesetzt, Array-Teil belegt entkoppelt
- [x] **P0-3** Marktspieler-Leistungsdaten + `prob`
- [x] **P0-4** Aufstellung setzen (Guard + `SET_LINEUP`) — Code fertig, DoD-Shadow läuft
- [x] **P0-5** Master-Prompt korrigieren — Edits + 5 neue Eval-Szenarien, Eval 17/17 grün

### Phase 2 — P1: Von „funktioniert" auf „gut" · Status: **in Arbeit** (seit 2026-09-24)
- [x] **P1-6** Kaufpreis & G/V aus Kickbase (`mvgl` — `prc` gibt es nicht, siehe §6)
- [x] **P1-7** Trends aus Payload statt 25 HTTP-Calls (`tfhmvt`, `sdmvt`) + Historien-Cache
- [x] **P1-8** Echte Form & Minuten (`/performance`) + Spieltags-Cache
- [x] **P1-9** Liga-Limits lesen — `mppu`/`tpc` echt, drei Felder ohne Quelle (§8/F6)
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
| **0** | `pytest` grün · Snapshot-Datei existiert · Eval-Gerüst lauffähig · alle 5 Fragen in §8 beantwortet **oder** mit belegtem Rest-Verfahren + entkoppeltem Folgepaket abgeschlossen (siehe §8.0) |
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
| `mvt` | MW-Trendrichtung (0/1/2) — **nur die Richtung, keine Höhe**; `tfhmvt`/`sdmvt` gibt es im Markt nicht (P1-7) | ignoriert |
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
| `GET /v4/leagues/{l}/players/{p}/performance` | `it[].ph[]` mit `day, p` (Punkte), `mp` (**Minuten, als String `"96'"`**), `md`, `t1/t2`, `st` (Einsatzart: 5=Startelf, 3=eingewechselt, 4=ohne Einsatz, 1=nicht im Kader), `mdst` (2=gespielt). ⚠️ **alle Saisons seit 2016/17 + alle kommenden Spieltage**, ~105 KB je Spieler | P1-8 |
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
| D7 | Kaufpreis nur aus eigenem `trade_log` | `app/application/run_tick_uc.py` (`_load_buy_history`) | zugeloste/App-Käufe ohne Kaufpreis ⇒ PROFIT-Exits & Transfer-Erfolge nicht steuerbar | P1-6 ✅ |
| D8 | 25 History-Calls/Tick für Daten, die im Payload stehen | `app/application/player_enrichment.py:104` | Ban-Risiko; 12 von 22 Marktspielern trotzdem ohne Trend | P1-7 ✅ |
| D9 | `avg_points_last5` ist in Wahrheit der Saison-Ø | `app/application/player_enrichment.py:221` | Bankdrücker sieht aus wie im Oktober | P1-8 ✅ |
| D10 | Kaderlimit hartkodiert `15` | `app/domain/kb_rules.py:40` | real 11–25, Admin-Einstellung | P1-9 ✅ (aktiver Pfad über `constraints.squad_limit`; `kb_rules.py` bleibt als toter Code unangetastet, §4.2) |
| D11 | Keine Aufstellungs-Aktion | Gateway/Executor | teuerste Regel (−100/Slot) ohne Ausführungspfad | P0-4 |
| D12 | Fester 120-min-Takt | `app/config.py:31`, `scheduler.py:60` | ~11 von 12 Ticks im Leerlauf; kann 20:35 statt 20:15 feuern | P1-10 |
| D13 | `avg_points_last5` verwirft negative Werte | `app/application/player_enrichment.py` (`_avg_points_proxy`) | `ap > 0`-Filter ⇒ Spieler mit −60 Saison-Ø sieht aus wie einer ohne Daten; das aussagekräftigste Signal fällt weg | P0-3 ✅ |

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

#### P0-0.6 — Eval-Gerüst + `eval`-Marker  *(Plan-Ergänzung)*

> **[Plan-Ergänzung 2026-09-23] Dieses Paket fehlte, obwohl der Plan es voraussetzt.**
> §7 beschreibt Phase 0 als „Discovery + Snapshot + **Eval-Gerüst**", §5 verlangt die
> Marker-Registrierung in `pyproject.toml`, und das DoD von Phase 1 lautet „Eval-Suite grün" —
> aber kein Paket baut sie. Ohne registrierten Marker scheitert jeder Test mit
> `@pytest.mark.eval` sofort an `--strict-markers`; P0-5 (Prompt) hätte sein eigenes DoD nicht
> erfüllen können.

1. `pyproject.toml`: `markers = ["eval: kostet echte LLM-Calls, nicht im Default-Run"]`
   und `-m 'not eval'` in `addopts`.
2. `tests/eval/scenarios.py` — feste, **synthetische** Szenarien. Bewusst nicht aus den
   Cassettes: eine Eval misst, ob der Prompt eine Regel befolgt; dafür darf nur ein Faktor
   variieren. Jedes Szenario nennt **erlaubte** und **verbotene** Aktionen plus die Regel, gegen
   die geprüft wird — ein Eval, das eine einzige Aktion erzwingt, misst Zufall und ist nach dem
   ersten Prompt-Feinschliff rot, ohne dass etwas kaputt wäre.
3. `tests/eval/test_prompt_eval.py` — 3 Läufe je Szenario, `temperature=0`. Dafür hat
   `AnthropicClient.submit_decision` einen optionalen `temperature`-Parameter bekommen
   (Default unverändert: der Produktivpfad bleibt beim API-Default). Ohne ihn wären drei Läufe
   bei `temperature=1` eine Rauschmessung — §9 nennt `temperature=0` als Gegenmaßnahme, ohne
   dass der Code sie konnte.
4. `tests/eval/test_scenarios_build.py` — läuft **im Default-Run**, ohne Kosten: baut jedes
   Szenario zum USER-JSON und prüft Kader, Markt, Teamwert, 33 %-Grenze und 11 aufgestellte
   Spieler. Ohne diesen Wächter fällt ein kaputtes Szenario erst im bezahlten Lauf auf.

**Startszenarien:** `debt_before_kickoff` (Konto −4,2 Mio, Anpfiff in 90 min → verkaufen, nicht
kaufen) · `healthy_and_quiet` (alles in Ordnung → HOLD ist legitim, Sofortverkauf nicht) ·
`injured_starter` (These gebrochen).
**DoD:** `pytest` ignoriert die Eval im Default-Lauf, `pytest -m eval` sammelt sie und
überspringt sie ohne `ANTHROPIC_API_KEY`. ✅
**Erster echter Lauf 2026-09-23 17:2x:** 6/6 grün in 2:44 min, 12 Modell-Calls, kein Fallback —
der aktuelle Master-Prompt besteht alle drei Szenarien. `pytest -m eval -s` zeigt je Szenario
die Aktionsverteilung und die erste Begründung.
**Ausbau:** P0-5 ergänzt Szenarien für die Regeln, die der korrigierte Prompt neu trägt
(Unterbietungsgrenze, Sofortverkauf zum vollen MW, `mvud`-Uhr).

#### P0-0.7 — Verifikation gegen echte Calls  *(Plan-Ergänzung)*

> **[Plan-Ergänzung 2026-09-23]** Jedes Phase-0-Artefakt einmal echt ausgeführt: Discovery live,
> `dump_keys`, `answer_open_questions`, `check_contract` (beide Quellen), `record_cassettes`,
> ein vollständiger Tick über `RunTickUseCase` und die Eval-Suite mit echtem Key. Drei Defekte
> kamen dabei heraus, die kein Unit-Test gezeigt hätte.

**V1 — Die Eval meldete grün, ohne das Modell je gefragt zu haben.** Mit einem ungültigen
API-Key fällt `AiDecisionEngine` per Design still auf HOLD zurück (in Produktion richtig, ein
Tick darf nicht crashen). In der Eval waren dadurch **5 von 6 Tests grün** — alle Szenarien, die
HOLD erlauben — und der sechste meldete einen HTTP 401 als *Regelverstoß*, was die Fehlersuche
in den Prompt gelenkt hätte. Eine Eval, die bei kaputtem Key grün ist, gibt einen Prompt-Merge
frei, ohne den Prompt getestet zu haben.
**Behoben:** Preflight über `verify_key()` bricht die Suite ab, bevor ein Szenario läuft (spart
zugleich 12 sinnlose Calls), plus `_reject_fallbacks()` vor jeder inhaltlichen Assertion.

**V2 — Die Belegdaten für F2 waren durch P0-0.3 zerstört.** Die Cassette vom 31.08. war die
einzige Quelle mit `prob`; die Neuaufnahme hat sie überschrieben, und Kickbase liefert das Feld
außerhalb der Spieltagswoche nicht. `answer_open_questions.py` zeigte danach „0/21 Einträge mit
`prob`" — die Antwort stand nur noch als Behauptung in der Doku.
**Behoben:** `docs/samples/market_prob_sample_2026-08-31.json` (22 Einträge, ohne Spielernamen)
wird als dritte Quelle gelesen. Sie ist zugleich die einzige *unabhängige* zweite Stichprobe
für F3 — ohne sie zählte die Auswertung Live-Dump und Cassette vom selben Tag doppelt.

**V3 — `temperature=0` war ungeprüft.** Der Parameter hätte still verschwinden können, ohne dass
etwas rot wird; drei Läufe je Szenario hätten dann Sampling gemessen statt Prompt-Treue.
**Behoben:** zwei Tests in `test_anthropic_client.py` (Default lässt ihn weg, gesetzt kommt er an).

**Bestätigt:** Der Live-Payload eines echten Ticks ist **strukturgleich** mit dem Snapshot aus
P0-0.4 (einziger Unterschied: `bought_at_price`/`bought_intent`, die der Snapshot synthetisch
setzt) — der Snapshot ist also repräsentativ. Alle vier Gap-Befunde reproduzieren sich live:
`team_value: 0`, `max_negative_allowed: 0`, `avg_points_last5` bei 19 von 20 Marktspielern `null`,
`start_probability_next` mit 3 distinkten Werten.

### PHASE 1 — P0: Bot handlungsfähig machen
> Ohne diese 5 Pakete ist der Bot nicht wettbewerbsfähig. Aufwand gesamt ~3–4 Tage.

#### P0-1 — Team-Value & Markt-Metadaten  *(höchster Hebel, kleinster Aufwand)*
**Behebt:** D1

> **Empirischer Beleg (Tick vom 2026-09-23 16:59, echter Modell-Call):** Das LLM hat die Lücke
> selbst erkannt und daraus die falsche Schlussfolgerung gezogen —
> *„team_value=0 bedeutet max_negative_allowed=0, Konto darf nicht weiter ins Minus. Ein
> weiterer Kauf ohne neues Cash ist daher regelwidrig."*
> Tatsächlich erlaubt die 33 %-Regel bei `tv` = 148.767.974 rund **−49 Mio**. Der Bot hält sich
> also für handlungsunfähig, obwohl er 49 Mio Spielraum hat, und entscheidet strukturell zu
> konservativ. D1 ist damit kein theoretischer Defekt: er verzerrt jede einzelne Entscheidung.
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

> **[Plan-Ergänzung 2026-09-23, zweite] Zwei Stellen, an denen der Plan sich auf Disziplin
> verlässt statt auf Code.**
>
> **1. „Vorher nicht scharf schalten" war ein Merkzettel, kein Mechanismus.** Der Master-Prompt
> bietet `ACCEPT_OFFER`/`DECLINE_OFFER` im Aktionsraum an, `incoming_offers` ist bis zur Klärung
> von F1 **immer leer** — also ist jede `offer_id`, die das Modell nennt, zwangsläufig erfunden,
> und der Executor hätte sie abgesetzt. Der Schutz war allein `dry_run=true`, und genau das stand
> am 23.09. auf `0` (§9.1). **Umgesetzt:** `_validate_against_context()` prüft jede vom Modell
> genannte ID gegen den Kontext, den es bekommen hat — unbekannte `offer_id` ⇒ HOLD mit
> Begründung. Dieselbe Prüfung fängt `BUY` auf einen Spieler, der nicht am Markt ist, und
> `SELL`/`SELL_LIST` für einen, der nicht im Kader steht. Die Sperre ist kein Verbot, sondern
> eine Deckungsprüfung: sobald echte Gebote im Payload stehen, öffnet sie sich von selbst
> (durch einen Test belegt).
>
> **2. Das Rest-Verfahren für F1 war nicht auslösbar.** §8/F1 verweist auf einen manuellen
> `inspect_endpoints`-Lauf — der müsste zufällig laufen, *während* ein Gebot offen ist, und
> Gebote laufen ab. Realistisch hätte niemand den Moment erwischt. **Umgesetzt:**
> `MarketPlayerDTO` läuft als einziges DTO mit `extra="allow"`, und `get_market()` meldet bei
> `ofc > 0` die unbekannten Felder des Listings als WARNING (landet damit auch im
> Dashboard-Log-Stream). Steht dort kein unbekanntes Feld, ist auch das ein Befund und wird
> als INFO protokolliert: dann liegt das Array nicht im `/market`-Payload und F1 braucht eine
> andere Quelle. Der Tick entscheidet dadurch nichts anders — er hält den Fund nur fest.
>
> **Nicht umgesetzt (bewusst): der zweite `ofc`-Aufruf über `GET /leagues/{l}/squad`.** Er ist
> überflüssig — eigene Listings stehen mitsamt `ofc` im `/market`-Payload (in der Cassette:
> Spieler 1809, `ofc: 0`, `u`-Objekt), und `_load_own_listings` liest sie ohnehin von dort.
> Ein zweiter Call pro Tick für dieselbe Zahl wäre gegen §9 (Rate-Limit) gerechnet worden.

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

> **[Plan-Ergänzung 2026-09-23, dritte] Beide Gap-Assertions waren in dieser Form nicht
> erfüllbar — das DoD hätte nie zugeschlagen.**
>
> **1. Gap 4 (`all(avg_points_last5 is not None)`) kann nicht grün werden.** 4 der 21
> Marktspieler der Cassette tragen weder `ap` noch `p` (Spieler ohne Einsatz). Für die ist
> `null` die *richtige* Antwort — ein erfundener Wert wäre genau der Fehler, den §9 verbietet.
> Mit `xfail(strict=True)` bleibt so ein Test dauerhaft „grün", und P0-3 wäre nie als fertig
> erkennbar gewesen. Der Defekt war auch nie „fehlt bei manchen", sondern
> „`to_market_player()` setzt für **alle** hart 0" — jeder Marktspieler sah gleich aus, der mit
> 178 Punkten wie der ohne jede Angabe. **Umgesetzt:** Die Assertion rechnet ihre Erwartung
> jetzt aus der Cassette aus — was Kickbase liefert, muss ankommen; wo nichts kommt, muss ein
> `missing_data:avg_points_last5`-Flag stehen; und die Werte müssen streuen.
>
> **Dazu ein eigener Defekt (neu, D13):** `_avg_points_proxy()` filterte auf
> `average_points > 0`. Die beiden Spieler mit negativem Saison-Ø (Platzverweis, Eigentor)
> landeten damit im selben Topf wie die ohne Daten — dabei ist −60 das aussagekräftigste
> Datum im ganzen Feld. Wer es nicht sieht, kauft den Spieler. Behoben, mit eigenem Test.
>
> **2. Gap 3 (Streuung der Startelf-Prognose) hat im Snapshot keine Datenquelle.** Der
> Snapshot läuft gegen die Cassette vom 23.09., und die enthält `prob` in **0 von 21** Items
> (§8/F2). Die `prob`-Stufe ist dort also prinzipiell nicht messbar, egal wie gut sie
> umgesetzt ist; die `sl`-Stufe liefert einen Ja/Nein-Wert und damit ebenfalls keine Streuung.
> **Umgesetzt:** Der `prob`-Pfad wird in `tests/application/test_start_probability_chain.py`
> gegen die Archiv-Stichprobe aus P0-0.7 geprüft (22 Einträge mit `prob`, Stufen 1–5) — die
> einzige erhaltene Quelle mit dem Feld. Der Haupt-Snapshot prüft stattdessen das, was in
> seiner Datenlage messbar *ist*: dass jede Prognose ihre Quelle nennt
> (`start_probability_source`) und das Heuristik-Flag nur noch dort steht, wo wirklich geraten
> wurde. Vorher hing es an jedem Spieler und trug damit keine Information.
>
> **3. Die `sl`-Stufe hätte die Call-Zahl pro Tick verdoppelt.** `GET /players/{p}` kostet
> einen Request **pro Spieler**; für Kader + kompletten Markt wären das ~29 zusätzliche
> Requests, obendrauf auf die 25 Historien-Calls aus D8. §9 führt genau das als Ban-Risiko,
> und P0-3 nennt keinen Deckel (der steht erst bei P1-7/P1-8). **Umgesetzt:** `sl` wird nur
> für die Auswahl geholt, die ohnehin Historien bekommt (Kader + Top-N Markt), und nur dort,
> wo `prob` fehlt. In der Spieltagswoche kostet die Kette damit **null** zusätzliche Requests
> — durch einen Test belegt.
>
> **4. Der Invarianten-Test aus P0-0.4 widerspricht §8/F2 und §9.** Er forderte „jeder
> Marktspieler hat eine Startelf-Wahrscheinlichkeit", begründet damit, ein fehlender Wert sei
> schlimmer als ein grober. Der Plan sagt an zwei anderen Stellen das Gegenteil, und er hat
> recht: eine 0.5 für einen Spieler mit unbekanntem Status ist von einer echten Prognose nicht
> zu unterscheiden, ein `null` mit Flag schon. Umgestellt auf „hat einen Wert **mit** Herkunft
> **oder** ein Flag, das sagt warum nicht". Das ist auch die Voraussetzung dafür, dass D6
> (unbekanntes `st`) überhaupt sauber lösbar ist — `PlayerStatus.UNKNOWN` bekommt bewusst
> **keine** Zahl.

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

> **[Plan-Ergänzung 2026-09-23, vierte] „Genau 11 IDs" macht den Guard genau dann unwirksam,
> wenn er gebraucht wird.**
>
> Schritt 5 verlangt als Vorvalidierung „genau 11 IDs, alle im Kader, Formation gültig,
> Positionszählung passt". Der echte Kader hat aber **acht** Spieler (§9.1, Befund 3) — bei
> weniger als elf gibt es keine vollständige Formation, und jede `SET_LINEUP`-Aktion wäre
> abgelehnt worden. Ausgerechnet in der Lage mit drei leeren Slots (−300 Punkte) hätte das
> Paket also nichts bewirkt. Dass der Zustand möglich ist, belegt Kickbase selbst:
> `lineup/overview` meldet `t="3-5-2"` **und** `lpc=8`.
> **Umgesetzt:** `validate_lineup` verlangt **höchstens** elf und eine Verteilung, die die
> Formation nicht *überschreitet*. `best_lineup` füllt so viele Slots wie möglich; der Guard
> schweigt, wenn mehr nicht geht (die restlichen Slots löst nur ein Kauf, und der ist eine
> Modell-Entscheidung).
>
> **Zweiter Befund: `best_lineup` hätte gesperrte Spieler aufgestellt.** Der Plan lässt die
> Auswahl über `prob × erwartete Punkte` laufen — beides aus der Anreicherung. Die fällt aber
> bei einem API-Fehler komplett aus (`_enrich_players` liefert dann `{}`), und dann gewinnt der
> verletzte Stammspieler mit 100 Punkten gegen den fitten Ersatz mit 60. **Umgesetzt:**
> `CANNOT_PLAY_STATUSES` als Untergrenze im Score — der Status steht im Kaderdatensatz und ist
> immer da. Gesperrte werden nicht gefiltert, nur ans Ende sortiert: bei einem Kader, der sonst
> nicht voll wird, sind 0 Punkte immer noch besser als −100.
>
> **Formationen sind nur zu einem Zehntel verifiziert.** Belegt ist allein `3-5-2` (aus der
> Cassette); die neun übrigen stammen aus den öffentlichen Kickbase-Systemen und sind gegen die
> API ungeprüft. Der Guard behält deshalb die von Kickbase gemeldete Formation bei und wechselt
> nur, wenn eine andere **mehr Slots besetzt** bekommt. Das ist im Shadow-Lauf mitzuprüfen.

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

**Eval-Lauf 2026-09-23 (nach dem Prompt-Merge): 17/17 grün, 32 Modell-Calls, 8:00 min.**
Aktionsverteilung je Szenario, jeweils 3/3 einstimmig:
`debt_before_kickoff` → SELL · `healthy_and_quiet` → HOLD · `injured_starter` → SELL_LIST ·
`open_lineup_slots` → **SET_LINEUP** · `instant_sale_before_deadline` → SELL ·
`bench_player_is_no_bargain` → HOLD (kein Kauf trotz 140 Punkten Schnitt) ·
`profit_peak` → SELL_LIST · `no_offers_means_no_accept` → HOLD.
Der aussagekräftigste Befund steht in der Begründung des Lineup-Szenarios: das Modell zitiert
`empty_slots: 1` und `points_at_risk: 100` wörtlich — die Felder aus P0-4 werden gelesen und
nicht nur mitgeschickt. Ebenso `profit_peak`: die Divergenz zwischen `trend_1d_pct` und
`trend_7d_pct` wird als Peak-Signal benannt, also genau nach §2.6 des Regelwerks.

> **[Plan-Ergänzung 2026-09-23, fünfte] Die Eval maß den falschen Pfad.**
> `temperature` stand nur im Eval-Wrapper `_DeterministicLlm`, nicht im Produktivpfad — der
> lief am API-Default. Doppelter Schaden: der Bot traf seine **echten** Entscheidungen weiter
> mit Sampling (bei gleicher Lage konnte eine andere Aktion herauskommen, ohne dass sich etwas
> geändert hatte), und die Eval hätte den Verlust der Einstellung nie gemeldet, weil sie sie
> selbst herstellte. P0-5 verlangt „`temperature=0` im Decision-Call sicherstellen" — das war
> über den Wrapper eben *nicht* sichergestellt.
> **Umgesetzt:** `AiDecisionConfig.temperature = 0.0` im Produktivpfad, Wrapper ersatzlos
> entfernt (die Eval fährt jetzt denselben Pfad wie die Produktion), plus
> `test_eval_measures_a_deterministic_path` als Wächter **vor** dem ersten bezahlten Szenario —
> ein Lauf, der nur Rauschen misst, ist das Geld nicht wert.

---

### PHASE 2 — P1: Von „funktioniert" auf „gut"
> Aufwand ~3 Tage. Ab hier wird der Bot rechenfähig statt nur regelkonform.

#### P1-6 — Kaufpreis & G/V aus Kickbase
**Behebt:** D7 · `prc`/`mvgl` → `SquadPlayer.buy_price`, `SquadPlayer.unrealized_pnl`.
`buy_history` aus dem `trade_log` bleibt **Intent-Quelle**, ist aber nicht mehr Preisquelle.
Fallback für fehlendes `prc`: `/managers/{m}/transfer` (`trp`).
**Test:** Konsistenz-Check über die Cassette: `mv - prc == mvgl`.
**DoD:** Jeder Kaderspieler im USER-JSON hat `bought_at_price` ≠ null.

> **[Plan-Ergänzung 2026-09-24] `prc` steht nicht im Squad-Payload — der Einstand wird aus `mvgl`
> zurückgerechnet.**
> §3.3 führte `prc` unter den verifizierten Squad-Keys. Die echte Cassette hat es nicht: die Keys
> sind `ap, iotm, lo, lst, mv, mvgl, mvt, p, pi, pim, pn, pos, sdmvt, st, stl, tfhmvt, tid`.
> Damit wäre der Plan-Test (`mv - prc == mvgl`) gar nicht ausführbar gewesen und das DoD
> unerreichbar — der Bot hätte weiter nur die selbst gekauften Spieler mit Einstand gesehen.
> **Umgesetzt:** `mvgl = mv - prc` wird umgestellt zu `buy_price = mv - mvgl`.
> **Belegt gegen eine unabhängige Quelle** (`/managers/{m}/transfer`, Feld `trp` = bezahlter
> Preis): Saibari 28.000.000 und Wolf 11.000.005, beide exakt identisch mit `mv - mvgl`.
> Nebenbefund derselben Prüfung: `tty` = 1 ist der Zugang, 2 der Abgang (Adam 12317 steht mit
> `tty=1` am 17.09. und `tty=2` am 22.09. im Log).
> **Folge für den Fallback:** `/managers/{m}/transfer` wird **nicht** in den Produktivpfad
> aufgenommen. `mvgl` deckt 8 von 8 Kaderspielern ab, die Transferhistorie nur 2 — sechs Spieler
> sind zugelost und tauchen dort nie auf. Genau die waren Defekt D7, der Fallback hätte sie also
> nicht gerettet, wohl aber einen HTTP-Call pro Tick gekostet. Er bleibt **Testquelle** für die
> Gegenprobe (`tests/infrastructure/kickbase/test_buy_price.py`).
> **Zweite Korrektur:** `SquadPlayer.buy_price` hatte Default `Decimal(0)`. Ein Einstand von 0
> weist den gesamten Marktwert als Gewinn aus — dieselbe Falle wie die Default-0 bei `team_value`
> (D1), nur pro Spieler. Jetzt `None` + `missing_data:bought_at_price`.

#### P1-7 — Trends aus Payload statt 25 HTTP-Calls
**Behebt:** D8 · `tfhmvt` (24 h €) und `sdmvt` (7 d €) übernehmen, in Prozent umrechnen.
`get_market_value_history` nur noch für `mv_max_30d` + 30-d-Trend, und nur für **Kader + Shortlist**
(nicht Top-10-nach-MW).
**Test (validiert die Feldsemantik empirisch):** der aus `tfhmvt` abgeleitete Prozentwert muss
(±0,1 pp) dem aus der History berechneten entsprechen — analog `sdmvt` gegen den 7-d-Trend.
**DoD:** Requests/Tick im Log messbar gesunken; alle Marktspieler haben 24-h/7-d-Trends.

> **[Plan-Ergänzung 2026-09-24] Beide Hälften des DoD waren so nicht erreichbar.**
>
> **(a) `tfhmvt`/`sdmvt` stehen nur im Squad-Payload, nicht im Markt.** Die verifizierten
> Market-Item-Keys sind `ap, dt, exs, fn, i, iposl, isn, mv, mvt, n, ofc, p, pim, pos, prc, st,
> tid` — vom Trend trägt das Item nur `mvt`, die *Richtung* (0/1/2) ohne Höhe. „Alle Marktspieler
> haben 24-h/7-d-Trends" ist mit Payload-Feldern also unmöglich; erreichbar ist es für **alle
> Kaderspieler**. Für Marktspieler bleibt es bei der Historie, und die bekommt weiter nur die
> Shortlist — der Rest trägt `null` + `missing_data`-Flag statt einer aus `mvt` geratenen Zahl.
>
> **(b) Die Call-Zahl wäre nicht gesunken.** Der Plan lässt die Historie für „Kader + Shortlist"
> stehen — also genau die Menge, die sie heute schon bekommt (Kader + Top-10). `tfhmvt`/`sdmvt`
> ersetzen zwar zwei der fünf Kennzahlen, aber `trend_3d`, `trend_30d` und `mv_max_30d` brauchen
> die Serie weiterhin. Netto: null eingesparte Requests, DoD nicht erfüllbar.
> **Umgesetzt:** ein **Tages-Cache für Marktwert-Historien**, gültig bis `mvud` (dem nächsten
> Update-Zeitpunkt aus dem Market-Root, P0-1). Kickbase schreibt Marktwerte einmal täglich fort;
> bei 120-min-Takt holte der Bot elf von zwölf Malen unveränderte Daten. Der Cache liegt in
> SQLite (`market_value_cache`, eine Zeile je Liga+Spieler) — dieselbe Infrastruktur, die P1-8
> für die Performance-Daten ohnehin verlangt. Ohne bekanntes `mvud` wird **nicht** gecacht: eine
> geratene Haltbarkeit ließe den Bot einen ganzen Marktwert-Zyklus lang die Bewegung verpassen,
> die er handeln soll. Der Enricher loggt pro Tick „X aus dem Cache, Y per HTTP" — das ist die
> vom DoD verlangte Messung.
>
> **Feldsemantik bestätigt, sogar exakt statt ±0,1 pp:** `tfhmvt` = `mv − mv[−2]` (Upamecano
> 5.697, Vortageswert 33.691.880), `sdmvt` = `mv − mv[−8]` (7.367, Wert vor 7 Tagen 33.690.210).
> Beides deckt sich auf den Euro mit der 365-Tage-Serie und damit mit `_trend(1)`/`_trend(7)`.
>
> **Vorrang der Quellen:** der Payload-Wert schlägt die Historie für 1 d/7 d. Er stammt aus dem
> Squad-Call dieses Ticks, die Serie womöglich aus dem Cache von heute Nachmittag.
>
> **Shortlist auf `ap` umgestellt** (der Plan fordert „nicht Top-10-nach-MW", nennt aber kein
> Kriterium): nach Marktwert landeten zuverlässig dieselben Stars in der Liste, während der
> billige Rohpunkte-Sammler — die PROFIT-These aus §2.6 — nie eine Historie bekam. Spieler ohne
> `ap` stehen hinten: „keine Daten" rechtfertigt keinen Request.

#### P1-8 — Echte Form & Minuten
**Behebt:** D9 · `/v4/leagues/{l}/players/{p}/performance` → `ph[].p`, `ph[].mp`, `ph[].day`.
Daraus: echtes `avg_points_last5`, `minutes_last5`, `starts_last5` (Rotationsrisiko).
`missing_data:avg_points_last5_using_season_avg` entfällt.
**Kostenkontrolle:** 1 Call/Spieler, nur Kader + Markt-Shortlist (Top-N nach `ap`, **nicht** nach MW),
mit **Tages-Cache in SQLite** — Spieltagspunkte ändern sich nur montags.
**Test:** Unit auf die Fenster-Mathematik bei < 5 Spieltagen Historie (muss `None` liefern, nicht verzerren).

> **[Plan-Ergänzung 2026-09-24] Drei Stellen, an denen die Umsetzung nach Planwortlaut
> danebengegangen wäre.**
>
> **(a) `mp` ist ein String mit Apostroph.** Der Plan nennt `ph[].mp` als „Minuten". Real steht
> dort `"96'"`, `"0'"`. Ein `int`-Feld im DTO wäre mit einem ValidationError ausgestiegen und
> hätte die **gesamte** Anreicherung mitgerissen — der Tick wäre ohne jedes Zusatzsignal
> weitergelaufen, ohne dass die Ursache irgendwo sichtbar wird.
>
> **(b) Die Response enthält alle Saisons *und* alle kommenden Spieltage.** `it[]` hat 11
> Gruppen ab 2016/17 (~105 KB je Spieler), und in der aktuellen Saison stehen alle 34 Spieltage —
> gespielt sind vier. „Die letzten fünf Einträge" hätte also über die **Zukunft** gemittelt und
> für jeden Spieler nahe null ergeben. Filter ist `mdst == 2`; über die ganze Cassette trennt er
> exakt (291 Einträge mit Minuten, alle `mdst == 2`; 30 ohne, alle `mdst == 0`). Gehalten wird
> nur die letzte `it`-Gruppe.
>
> **(c) Der Plan-Test hätte das Paket wirkungslos gemacht.** „< 5 Spieltage ⇒ `None`" heißt am
> 4. Spieltag einer Saison: **kein einziger Spieler** hat eine Form — also exakt der Zustand von
> D9, nur mit anderem Etikett. Die Absicht („nicht verzerren") ist richtig, die Umsetzung nicht.
> Stattdessen wird das kürzere Fenster gerechnet und mit `form_matchdays_counted` ausgewiesen,
> plus `missing_data:avg_points_last5_partial_window`. Ein Schnitt aus zwei Spieltagen ist damit
> von einem aus fünf unterscheidbar — was ein `None` gerade nicht leistet. Nur **ohne einen
> einzigen** gespielten Spieltag bleibt es bei `None` bzw. beim Saison-Ø.
>
> **`starts_last5` kommt aus `st` im Spieltags-Eintrag** — nicht zu verwechseln mit dem
> Spielerstatus aus `PlayerStatus`. Die Bedeutung ist undokumentiert, über die Minutenverteilung
> derselben Cassette aber eindeutig: `st=5` Startelf (n=217, Median 90 min), `st=3` eingewechselt
> (n=32, Median 22), `st=4` ohne Einsatz (n=38, Median 0), `st=1` nicht im Kader (n=4, alle 0).
> Unbekannte Werte zählen **nicht** als Startelf: `starts_last5` misst Rotationsrisiko, ein zu
> niedriger Wert warnt, ein zu hoher beruhigt fälschlich.
>
> **Cache-Haltbarkeit ist `next_matchday_start`, nicht „Tages-Cache".** Spieltagspunkte stehen
> fest, sobald der Spieltag durch ist. Läuft gerade einer, liegt der nächste Anpfiff in der
> Vergangenheit — dann wird nicht geschrieben und jeder Tick sieht die Live-Punkte. Ein
> Kalendertag-TTL hätte den Bot genau während des Spieltags eingefroren.

#### P1-9 — Liga-Settings lesen
**Behebt:** D10 · `/leagues/{l}/settings` + `/me → tpc[]` ⇒ `constraints.squad_limit`,
`constraints.club_limit`, `constraints.underpay_blocked`, `constraints.scoring_mode`,
`constraints.players_per_club: {tid: n}`.
`kb_rules.py:40` (`_MAX_SQUAD_SIZE = 15`) wird Konfiguration statt Konstante.
**Test:** Contract auf die Settings-Response · Unit: BUY, der das Vereinslimit **inkl. offener Gebote**
sprengt, wird als Verstoß erkannt.
**Prompt-Nachzug:** §1.1 auf `constraints.*` umstellen (kleiner Prompt-Edit, eigener Commit).

> **[Plan-Ergänzung 2026-09-24] Der Pakettext nennt einen Endpunkt, den es nicht gibt — und drei
> Felder, die keine Quelle haben.**
>
> §3.4 und der Log-Eintrag zu P0-0.1 halten bereits fest: `GET /v4/leagues/{l}/settings` liefert
> HTTP 500 `NotFound`. Der Pakettext führt ihn trotzdem als Primärquelle und verlangt einen
> „Contract auf die Settings-Response". Nach Wortlaut umgesetzt wäre das Paket gegen einen 500er
> gelaufen.
>
> **Was wirklich abrufbar ist:** `mppu` (Kaderlimit, hier **16** — der Code hatte 15) und `tpc[]`
> (Spieler je Verein) aus `/leagues/{l}/me`. `mppu` steht zusätzlich in `/leagues/{l}/squad`.
>
> **Was in keiner Response steht:** `club_limit`, `underpay_blocked`, `scoring_mode`. Geprüft
> wurden alle 16 Discovery-Dumps. Der einzige Kandidat fürs Vereinslimit wäre `clpc`, das in
> `/ranking` den Wert 11 trägt — in `/lineup/overview` aber 0, bei acht aufgestellten Spielern.
> Zwei Endpunkte, zwei Bedeutungen: als Beleg untauglich. `upe` (`/me`, `false`) wäre ein
> Kandidat für Underpay, `gpm`/`isp` für den Modus — alle drei unverifiziert.
>
> **Umgesetzt nach §9 (`None` + Flag, nie Default):** die drei Felder stehen mit `null` und einem
> `missing_data:constraints.*`-Flag im Payload. Ein geratenes Vereinslimit ist in **beide**
> Richtungen teuer: zu niedrig blockiert gültige Käufe dauerhaft, zu hoch lässt Kickbase das
> Gebot ablehnen und der Tick ist verbraucht. `club_limit` ist über `KB_CLUB_LIMIT`
> konfigurierbar — der Wert steht in den Admin-Einstellungen der Liga und ist ablesbar, nur nicht
> abrufbar. Neue offene Frage: **F6** in §8.
>
> **Der geforderte Unit-Test bekommt eine zweite Hälfte.** „BUY, der das Vereinslimit sprengt,
> wird als Verstoß erkannt" ist ohne bekanntes Limit nicht prüfbar. Genauso wichtig ist der
> umgekehrte Fall: **ohne Limit darf kein Verstoß behauptet werden.** Gäbe `club_room_left()`
> dort 0 zurück, kaufte der Bot nie wieder einen zweiten Spieler desselben Vereins — eine
> Selbstblockade, die wie eine Regel aussieht und deshalb niemandem auffällt.
>
> **`kb_rules.py` wird nicht angefasst.** Der Pakettext sagt „`_MAX_SQUAD_SIZE = 15` wird
> Konfiguration statt Konstante", §4.2 führt dieselbe Datei als toten Code mit „nicht anfassen,
> nicht erweitern". Aufgelöst zugunsten von §4.2: D10 wirkt im **aktiven** Pfad über
> `constraints.squad_limit` im USER-JSON. Die Konstante im Heuristik-Pfad bleibt stehen, bis
> dieser Pfad entweder entfernt oder reaktiviert wird.

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
| F6 | In der App *Liga → Admin-Einstellungen* öffnen: Spielerlimit pro Verein, Underpay-Option, Wertungsmodus ablesen | Vereinslimit als `KB_CLUB_LIMIT` setzen, Rest in §8/F6 eintragen | nichts (Paket ist entkoppelt), verbessert nur die Prompt-Qualität |

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

### F6 — Wo stehen Vereinslimit, Underpay-Regel und Wertungsmodus?
**Status:** 🔴 offen · **[Ergänzung 2026-09-24, aus P1-9]**
**Belegt ausgeschlossen:** `GET /v4/leagues/{l}/settings` → HTTP 500 `NotFound`. Keiner der 16
Discovery-Dumps trägt ein Feld, das sich einem der drei Werte zuordnen ließe. `/me` liefert
`mppu` (Kaderlimit, 16) und `tpc[]` (Spieler je Verein) — mehr nicht.
**Kandidaten, alle unverifiziert:** `clpc` (Vereinslimit?) steht in `/ranking` auf 11, in
`/lineup/overview` aber auf 0 bei acht aufgestellten Spielern — zwei Bedeutungen in zwei
Endpunkten, als Beleg untauglich. `upe` (`/me`, `false`) für Underpay. `gpm: 1` / `isp: false`
für den Modus.
**Rest-Verfahren:** in der App unter *Liga → Admin-Einstellungen* ablesen. Vereinslimit dann als
`KB_CLUB_LIMIT` setzen; Underpay und Modus in §8/F6 eintragen und im Prompt nachziehen.
**Paket entkoppelt (§8.0/c):** alle drei stehen als `null` + `missing_data:constraints.*` im
Payload. `club_room_left()` gibt ohne Limit `None` zurück, nicht 0 — der Bot behauptet damit
keinen Verstoß, den er nicht kennt, und blockiert sich auch nicht selbst.
**Blockiert:** nichts. Betrifft die Prompt-Formulierung und P2-12 (Risikoprofil bei H2H).

---

## 9. Risiken & Kill-Switches

| Risiko | Gegenmaßnahme |
|---|---|
| `prob`-Richtung invertiert | F2 gegen die App verifizieren, nicht gegen Intuition. Mapping als benannte Konstante mit Kommentar. |
| Kickbase ändert Feldnamen | `scripts/check_contract.py` monatlich + Contract-Tests brechen laut statt still. Fehlendes Feld ⇒ `None` + `missing_data`-Flag, **nie Default-0** (das war D1). |
| Aufstellungs-Write zerstört eine gute Elf | Vorvalidierung im Executor (nicht im Prompt) · 7 Tage Shadow · ENV `KB_LINEUP_WRITES_ENABLED=false` |
| Rate-Limit / Ban | P1-7 senkt die Call-Zahl netto. P1-8/P2-11 nur mit Tages-Cache. `AsyncRateLimiter` bleibt harter Deckel. |
| Prompt-Regression durch Sampling | Eval mit 3 Läufen/Szenario vor jedem Prompt-Merge · `temperature=0` |
| Halbfertiger Zustand in Produktion | Ein Paket = ein Merge = ein Deploy. `dry_run=true` bleibt Default, bis Phase 1 komplett durch den Shadow ist. ⚠️ **Ist-Zustand 2026-09-23: `dry_run = 0` in der Produktions-DB** — siehe §9.1. |
| Rechtlich/ToS | unverändert: Privatnutzung, konservatives Rate-Limit (siehe `PROJEKT.md` §1.3/§1.4) |

---

### 9.1 Betriebsbefunde aus dem Verifikationslauf 2026-09-23

Beim echten Tick (P0-0.7) gegen eine DB-Kopie sind zwei Zustände aufgefallen, die nichts mit den
Paketen zu tun haben, aber die Wirksamkeit des ganzen Plans betreffen:

1. **`dry_run = 0`** — der Bot war scharf geschaltet, obwohl Phase 1 nicht begonnen hat. §9 sieht
   `dry_run=true` bis zum Ende des Shadow-Laufs vor. ✅ *Am 23.09. auf `1` gestellt — der
   7-Tage-Shadow-Lauf kann damit ab jetzt mitlaufen.*
2. **Der hinterlegte Anthropic-Key war ungültig** (`HTTP 401: API key is invalid`). Jeder Tick
   endete im Fallback-HOLD — der Bot entschied gar nichts. Im `trade_log` steht das als
   gewöhnliches HOLD, ohne Alarm. ✅ *Behoben am 23.09. 16:57 über `/setup/anthropic`;
   Kontroll-Tick um 16:59 lieferte eine echte, begründete Entscheidung.*
   → **Kandidat für ein eigenes Paket bleibt:** ein dauerhafter LLM-Fehler muss sichtbar werden
   (Dashboard-Warnung oder Mail), nicht als HOLD durchgehen. Genau dieser Zustand lief
   unbemerkt.
3. Nebenbefund aus dem Payload: **Kader hat 8 Spieler, `starting_xi_count: 8`.** Drei leere
   Positionen = −300 Punkte am nächsten Spieltag (P0-4).

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
| 2026-09-23 | P0-0.6 | **Ergaenzung:** Eval-Geruest + `eval`-Marker + `temperature`-Parameter | §7 und das Phase-1-DoD setzen eine Eval-Suite voraus, es gab aber kein Paket dafuer. Ohne registrierten Marker scheitert jeder Eval-Test an `--strict-markers`; ohne `temperature=0` sind 3 Laeufe je Szenario eine Rauschmessung. |
| 2026-09-23 | P0-0.7 | **Ergaenzung:** alles gegen echte Calls verifiziert | Eval meldete mit ungueltigem Key 5 von 6 Tests gruen (Preflight + Fallback-Guard ergaenzt). F2-Belegdaten waren durch die Cassette-Neuaufnahme zerstoert (Archiv-Stichprobe angelegt). `temperature=0` war ungeprueft. Live-Payload ist strukturgleich mit dem Snapshot. Betriebsbefunde in §9.1. |
| 2026-09-23 | — | Kontroll-Tick nach Key-Erneuerung (`POST /api/scheduler/trigger`, dry_run=1) | Voller pfad gruen: echter modell-call in ~20 s, HOLD mit schluessiger begruendung, trade_log id 5 mit `dry_run: true`. **D1 empirisch belegt:** das LLM nennt `max_negative_allowed=0` selbst als kaufblocker, obwohl real ~49 Mio minus erlaubt waeren. |
| 2026-09-23 | P0-0.6 | Eval-Suite erstmals mit gueltigem key ausgefuehrt | 6/6 gruen, 12 calls, 2:44 min, kein fallback. der aktuelle master-prompt besteht alle drei szenarien trotz der fehler aus §4.1. ausgabe der gewaehlten aktionen nachgeruestet (`-s`), sonst verschenkt ein bezahlter lauf seinen befund. |
| 2026-09-23 | P0-1 | Team-Value & Markt-Metadaten, `MarketSnapshot`, `Squad.team_value`/`budget` entfernt | **D1 geschlossen:** `team_value` 0 -> 148.767.974, `max_negative_allowed` 0 -> -48.968.009 im Payload. **Loop-Stopp aus dem Plan geprueft:** `mvud` ist der *naechste* Update-Zeitpunkt (Cassette 16:09 Z -> `mvud` 20:00 Z), §3.1 stimmt, P1-10 kann darauf bauen. `dt` aus dem Market-Root deckt sich exakt mit dem bisherigen `list_matchdays()`-Ergebnis (2026-10-09T18:30Z) -> **ein HTTP-Call weniger pro Tick**, Liste bleibt Fallback fuer veraltetes `dt`. Die in P0-0.4 vorgemerkte Zeitquelle ist mit umgezogen: `MarketPlayer` traegt jetzt das rohe `exs`, `expires_at(now)` rechnet damit — der Snapshot-Workaround `_market_with_fixed_expiry` konnte ersatzlos entfallen. Dashboard zeigte `squad.team_value` (immer 0) und laeuft jetzt ueber den Snapshot — bei **gleicher** Call-Zahl, weil `nps` die Kadergroesse gleich mitliefert. |
| 2026-09-23 | P0-2 | **Ergaenzung:** `ofc` als `offer_count` im Payload, Kontext-Validierung der LLM-IDs, Diagnose-Hook fuer das ungeklaerte Gebots-Array | `has_offers` haing am `offers`-Tupel, das bis F1 immer leer ist — ein Listing mit vier Bietern galt als „keine Gebote" und waere in den Sofortverkauf gelaufen. Jetzt aus `ofc`. **Der Plan verliess sich an zwei Stellen auf Disziplin:** „nicht scharf schalten" war ein Merkzettel (jede `offer_id` des Modells ist zwangslaeufig erfunden, der Executor haette sie abgesetzt — einziger Schutz war `dry_run`, das am 23.09. auf `0` stand), und das Rest-Verfahren fuer F1 haing an einem manuell getimten Skriptlauf waehrend eines offenen Gebots. Beides jetzt als Code. Zweiter `ofc`-Call ueber `/leagues/{l}/squad` ist ueberfluessig: eigene Listings stehen mit `ofc` im `/market`-Payload. |
| 2026-09-23 | P0-3 | **Ergaenzung:** Leistungsdaten durchgereicht, Startelf-Quellenkette mit Herkunftsangabe, `PlayerStatus.UNKNOWN`, `_avg_points_proxy` korrigiert (D13) | **Beide Gap-Assertions waren nicht erfuellbar** — Gap 4 verlangt Werte fuer 4 Spieler, fuer die Kickbase keine liefert; Gap 3 misst Streuung in einer Cassette ohne `prob`. Mit `xfail(strict=True)` waeren beide dauerhaft „gruen" geblieben und das Phase-1-DoD haette nie zugeschlagen. Neu gefasst: Gap 4 rechnet die Erwartung aus der Cassette, Gap 3 laeuft gegen die Archiv-Stichprobe mit `prob`. **Neuer Defekt D13:** `_avg_points_proxy` filterte `> 0` — die beiden Spieler mit negativem Saison-Ø (−4, −60) galten als datenlos. **Kostendeckel:** `sl` haette ~29 Requests/Tick gekostet (§9 Ban-Risiko); jetzt nur fuer die Auswahl, die ohnehin Historien bekommt, und nur wo `prob` fehlt — in der Spieltagswoche null Zusatz-Calls. Payload-Effekt: `avg_points_last5` 1/21 -> 17/21 gefuellt, `start_probability_next` 3 -> 4 distinkte Werte mit ausgewiesener Quelle (10x `lineup_prediction`, 11x `injury_status_heuristic`). |
| 2026-09-23 | P0-4 | **Ergaenzung:** `app/domain/lineup.py`, Startelf-Guard, `SET_LINEUP`, Executor-Vorvalidierung, Kill-Switch `KB_LINEUP_WRITES_ENABLED` | **„Genau 11 IDs" haette den Guard unwirksam gemacht:** der echte Kader hat 8 Spieler, also gibt es keine vollstaendige Formation — ausgerechnet bei 3 leeren Slots (-300 Punkte) waere jede Aktion abgelehnt worden. Jetzt „hoechstens 11, Formation nicht ueberschritten". **Zweiter Befund:** `best_lineup` haette gesperrte Spieler aufgestellt, sobald die Anreicherung ausfaellt (`_enrich_players` liefert dann `{}`) — der Status ist jetzt Untergrenze im Score. Payload zeigt neu `lineup` mit `empty_slots: 3`, `points_at_risk: 300`, `allowed_formations`. Von den 10 Formationen ist nur `3-5-2` gegen die API verifiziert; der Guard behaelt die gemeldete bei, solange keine andere mehr Slots besetzt. |
| 2026-09-23 | P0-5 | Master-Prompt gegen §4.1 korrigiert, `SET_LINEUP` + zweite Uhr aufgenommen, USER-JSON-Beispiel auf den echten Payload gezogen, `temperature=0` im Produktivpfad, 5 neue Eval-Szenarien | Alle 10 Falschaussagen aus §4.1 sind raus (durch Test abgesichert: `test_corrected_claims_are_gone_from_the_prompt`). `temperature` war nur in der Eval gesetzt — der Produktivpfad lief am API-Default, damit war jede Entscheidung unreproduzierbar und ein Prompt-Merge nicht belegbar; jetzt `AiDecisionConfig.temperature = 0.0`. Der Waechter `test_scenarios_have_eleven_players_in_the_starting_xi` haette das SET_LINEUP-Szenario blockiert (es braucht per definitionem eine unvollstaendige Elf) — geloest ueber `Scenario.expects_full_lineup`, das gleichzeitig erzwingt, dass ein solches Szenario `SET_LINEUP` auch erlaubt. Prompt-Umfang: ~3.900 Tokens Cache-Prefix. **Offen:** der bezahlte Eval-Lauf (8 Szenarien x 4 = 32 Calls). |
| 2026-09-23 | P0-5 | Eval-Suite gegen den korrigierten Prompt ausgefuehrt | **17/17 gruen, 32 calls, 8:00 min**, alle acht szenarien 3/3 einstimmig. `open_lineup_slots` liefert SET_LINEUP und begruendet es mit `empty_slots: 1` / `points_at_risk: 100` — die P0-4-felder werden gelesen, nicht nur mitgeschickt. `bench_player_is_no_bargain` kauft den 140-punkte-mann mit 5 % startelf-chance nicht. **Befund beim start:** der wrapper `_DeterministicLlm` und die neue produktiv-`temperature` kollidierten — die eval mass bis dahin einen pfad, den es in produktion nicht gab. Wrapper entfernt, waechter-test davor. |
| 2026-09-24 | P1-6 | **Ergaenzung:** Einstand aus `mvgl` zurueckgerechnet statt aus `prc` gelesen, `unrealized_pnl` neu, Default-0 entfernt | **`prc` steht nicht im Squad-Payload** — §3.3 fuehrte es als verifiziert, die Cassette widerspricht. Der Plan-Test (`mv - prc == mvgl`) waere nicht ausfuehrbar gewesen. Umkehrung `buy_price = mv - mvgl` gegen `/transfer` (`trp`) belegt: Saibari 28.000.000, Wolf 11.000.005, beide exakt. **Der im Plan vorgesehene `/transfer`-Fallback faellt weg:** `mvgl` deckt 8/8 Kaderspieler, die Transferhistorie nur 2 — sechs sind zugelost und stehen dort nie, also genau der Fall von D7. Er bleibt Testquelle. `buy_price` hatte Default `Decimal(0)`; ein Einstand von 0 weist den ganzen Marktwert als Gewinn aus (D1 pro Spieler) — jetzt `None` + Flag. Payload: `bought_at_price` 1/8 -> 8/8, `unrealized_pnl` neu. |
| 2026-09-24 | P1-7 | **Ergaenzung:** `tfhmvt`/`sdmvt` fuer Kaderspieler, Historien-Cache bis `mvud`, Shortlist nach `ap` statt nach Marktwert | **Beide DoD-Haelften waren so nicht erreichbar.** (a) Die Market-Items tragen `tfhmvt`/`sdmvt` **nicht** — nur `mvt`, die Richtung ohne Hoehe. „Alle Marktspieler haben 24-h/7-d-Trends" ist ueber den Payload unmoeglich; erreichbar ist es fuer alle Kaderspieler. (b) Der Plan laesst die Historie fuer „Kader + Shortlist" stehen, also genau die Menge von heute — netto null eingesparte Requests. Der Hebel ist der **Cache**: der Marktwert aendert sich 1x taeglich, `mvud` nennt den Zeitpunkt, bei 120-min-Takt holte der Bot 11 von 12 Malen unveraenderte Daten. Ohne bekanntes `mvud` wird nicht gecacht. **Feldsemantik exakt bestaetigt** (nicht nur ±0,1 pp): `tfhmvt` = `mv - mv[-2]`, `sdmvt` = `mv - mv[-8]`, beide auf den Euro deckungsgleich mit der 365-Tage-Serie. **Payload schlaegt Historie** fuer 1 d/7 d — die Serie kann aus dem Cache kommen, `tfhmvt` ist immer frisch. Payload-Effekt: die Kader-Trends streuen erstmals (vorher 8x 0,02 % aus derselben Fake-Serie); Marius Wolf steht mit -13,2 % auf 7 Tagen da, wo vorher +0,02 % stand. |
| 2026-09-24 | P1-8 | **Ergaenzung:** `/performance` angebunden, Fenster ueber gespielte Spieltage, `minutes_last5`/`starts_last5`/`form_matchdays_counted` neu, Spieltags-Cache bis `next_matchday_start` | **Drei Stellen haetten nach Planwortlaut nicht funktioniert.** (a) `mp` kommt als String mit Apostroph (`"96'"`) — ein int-Feld waere mit ValidationError ausgestiegen und haette die ganze Anreicherung mitgerissen. (b) Die Response traegt 11 Saisons *und* alle kommenden Spieltage; „die letzten fuenf Eintraege" haette ueber die Zukunft gemittelt. Filter `mdst == 2` trennt exakt (291 mit Minuten / 30 ohne). (c) Der Plan-Test „< 5 Spieltage ⇒ `None`" haette am 4. Spieltag **jeden** Spieler ohne Form gelassen — D9 mit neuem Etikett. Jetzt wird das kuerzere Fenster gerechnet und als `form_matchdays_counted` + Partial-Flag ausgewiesen. `starts_last5` aus `st` (5=Startelf, Median 90 min gegen 22 bei `st=3`), Unbekanntes zaehlt nicht als Start. Cache-Grenze ist `next_matchday_start`: waehrend eines laufenden Spieltags wird nicht geschrieben, sonst saehe der Bot die Live-Punkte nicht. Payload: `avg_points_last5` ist echte Form, `minutes_last5`/`starts_last5` neu bei 8/8 Kader + 10/21 Markt. |
| 2026-09-24 | P1-9 | **Ergaenzung:** `mppu`/`tpc` gelesen, `LeagueConstraints` neu, drei Felder ohne Quelle als `null` + Flag, `KB_CLUB_LIMIT` als Konfigurationsweg | **Der Pakettext nennt einen Endpunkt, den es nicht gibt.** `/leagues/{l}/settings` → HTTP 500 `NotFound` (steht schon in §3.4 und im P0-0.1-Log, P1-9 fuehrte ihn trotzdem als Quelle inkl. „Contract auf die Settings-Response"). Abrufbar: `mppu` = **16** (Code hatte 15) und `tpc[]`. **Nicht abrufbar:** Vereinslimit, Underpay, Modus — alle 16 Discovery-Dumps geprueft. `clpc` als Kandidat verworfen: 11 in `/ranking`, 0 in `/lineup/overview` bei 8 aufgestellten. Neue offene Frage **F6**. Der geforderte Unit-Test bekam eine zweite Haelfte: ohne bekanntes Limit darf **kein** Verstoss behauptet werden — `club_room_left()` gibt `None` statt 0, sonst kauft der Bot nie wieder einen zweiten Spieler desselben Vereins. `kb_rules.py` blieb unangetastet (Widerspruch zu §4.2 zugunsten von §4.2 aufgeloest). |

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
