# API-Notizen — Kickbase v4

> Referenz für den Optimizing-Plan (`docs/optimizing_plan.md`). Enthält **nur verifizierte
> Beobachtungen aus echten Payloads**, keine Vermutungen aus fremder Doku.
>
> **Letzter Discovery-Lauf:** 2026-09-23 (Liga `Noob_League`, Spieltag 5, Saison 26/27)
> Erhoben mit `python -m scripts.inspect_endpoints`, ausgewertet mit
> `python -m scripts.dump_keys` und `python -m scripts.answer_open_questions`.

---

## 1. Endpoint-Status (Stand 2026-09-23)

| Endpunkt | HTTP | Anmerkung |
|---|---|---|
| `POST /v4/user/login` | 200 | `tkn`, `tknex`, `u{i,em,n}` |
| `GET /v4/leagues/selection` | 200 | `it[]` mit `i`, `n`, `b` |
| `GET /v4/leagues/{l}/me` | 200 | **Liga-Settings + Vereinszähler** — siehe §3 |
| `GET /v4/leagues/{l}/managers/{m}/squad` | 200 | Kader inkl. `prc`, `mvgl`, `tfhmvt`, `sdmvt` |
| `GET /v4/leagues/{l}/squad` | 200 | derselbe Kader, andere Keys + `ofc` + `mppu` |
| `GET /v4/leagues/{l}/market` | 200 | Transfermarkt, Root-Metadaten `tv`/`mvud`/`dt`/`day` |
| `GET /v4/leagues/{l}/lineup` | 200 | aufgestellte Spieler + letzte 4 Spieltagspunkte |
| `GET /v4/leagues/{l}/lineup/overview` | 200 | **Formation `t`** + Aufstellungs-Deadline `lis` |
| `GET /v4/leagues/{l}/lineup/selection` | 200 | leeres `it` — kein Nutzen erkennbar |
| `GET /v4/leagues/{l}/ranking` | 200 | Ligatabelle inkl. `lp[]` = Aufstellungen der Rivalen |
| `GET /v4/leagues/{l}/managers/{m}/transfer` | 200 | Transferhistorie mit Preisen |
| `GET /v4/leagues/{l}/players/{p}` | 200 | Spielerdetail inkl. `sl`, `mdsum[]`, `sec` |
| `GET /v4/leagues/{l}/players/{p}/performance` | 200 | alle Saisons, Spieltag für Spieltag |
| `GET /v4/leagues/{l}/players/{p}/marketvalue/365` | 200 | Marktwert-Zeitreihe |
| `GET /v4/competitions/1/matchdays` | 200 | **Kompletter Spielplan**: 34 Spieltage, 306 Paarungen mit `t1`/`t2`/`dt`/`st` — siehe §3 |
| `GET /v4/competitions/1/table` | 200 | Bundesliga-Tabelle (FDR-Grundlage), **unsortiert** — siehe §3 |
| `GET /v4/leagues/{l}/settings` | **500** | `{"err":2,"errMsg":"NotFound"}` — **existiert nicht** |
| `GET /v4/leagues/{l}/market/{p}/offers` | **405** | `Allow: POST` — nur Gebot abgeben |
| `GET /v4/leagues/{l}/market/{p}` | **405** | `Allow: DELETE` — nur Listing zurückziehen |

⚠️ `GET /v4/bonus/collect` wurde **nicht** abgerufen — der GET sammelt vermutlich tatsächlich ein.

---

## 2. Korrekturen am Optimizing-Plan §3

| Plan §3 sagt | Realität 2026-09-23 |
|---|---|
| `GET /v4/leagues/{l}/settings` liefert `amd`, `isp`, `gpm`, `lnm`, `mpst`, `mppu` | Endpunkt gibt **500 NotFound**. Dieselben Felder stehen in `/me` (`isp`, `gpm`, `lnm`, `mgm`, `mgc`, `mppu`) und `/leagues/{l}/squad` (`mppu`). |
| Market-Item hat `prob` (Startelf-Wahrscheinlichkeit) | Im Lauf vom 23.09. in **0 von 20** Items vorhanden; in der Cassette vom 31.08. in 22 von 22. Siehe §4/F2. |
| Market-Item hat `exs` (Restlaufzeit) | Nur bei **fremden/Kickbase-Listings**. Eigene Listings tragen kein `exs`. |
| `/players/{p}` liefert `sl` als „Startelf-Prognose, Quelle `plpt`" | Bestätigt, aber `sl` ist ein **bool**, nicht eine 5-stufige Skala. `plpt` = `"Ligainsider"`. |
| `/performance` liefert `it[].ph[]` mit `mp` (Minuten) | Bestätigt — `mp` ist ein **String** im Format `"0'"`, nicht eine Zahl. |

---

## 3. Felder, die der Bot heute nicht liest (Fundstellen)

### `GET /v4/leagues/{l}/me` — Liga-Settings ohne Extra-Call
```json
{"b": -380069, "bs": 3, "lnm": "Noob_League", "gpm": 1, "isp": false, "adm": false,
 "mgm": 18, "mgc": 4, "mppu": 16, "rnkm": 1, "un": 81, "cpi": "1", "cd": "DE",
 "tpc": [{"tid": "2", "npt": 2, "tim": "…svg"}]}
```
| Feld | Bedeutung (abgeleitet) | Paket |
|---|---|---|
| `b` | Kontostand (kann negativ sein) | genutzt ✅ |
| `mppu` | **max players per user = Kaderlimit** (hier 16) | P1-9 (ersetzt die hartkodierte 15) |
| `mgm` / `mgc` | max / aktuelle Mitgliederzahl | P2-12 |
| `gpm` | Spielmodus-Kennung (hier `1`) | P1-9 (Saisonpunkte vs. H2H) |
| `isp` | Liga-Flag (hier `false`) | P1-9 |
| `tpc[]` | **Spieler je Verein** (`tid`, `npt`) | P1-9 (Vereinslimit) |

### `GET /v4/leagues/{l}/squad` — zweite Kader-Sicht
Keys: `mppu`, `it[]` mit `i, n, ap, p, mv, mvgl, mvt, sdmvt, tfhmvt, lo, lst, mdst, st, stl, tid, iotm, ofc`
**`ofc` pro eigenem Spieler = Anzahl eingegangener Gebote.** Das ist der Trigger für P0-2.

### `GET /v4/leagues/{l}/lineup/overview`
```json
{"t": "3-5-2", "lis": "2026-10-09T18:30:00Z", "lpc": 8, "clpc": 0, "mdln": "5 Match Day",
 "lp": [{"pi": "2977", "lo": 0, "pos": 1, "mv": 13491277, "t1": "29", "t2": "9", …}]}
```
| Feld | Bedeutung | Paket |
|---|---|---|
| `t` | **aktuelle Formation** — Pflichtfeld im POST /lineup | P0-4 |
| `lis` | **Aufstellungs-Deadline** (= Spieltagsstart) | P0-4 / P1-10 |
| `lpc` | Zahl aufgestellter Spieler (11 = vollständig) | P0-4 |
| `lp[].t1` / `t2` | Heim-/Auswärtsteam des nächsten Spiels dieses Spielers | P2-11 |

### `GET /v4/leagues/{l}/players/{p}`
| Feld | Bedeutung | Paket |
|---|---|---|
| `sl` | Startelf-Prognose (**bool**), Quelle `plpt` = „Ligainsider" | P0-3 |
| `mdsum[]` | Spiele mit `t1`, `t2`, `t1g`, `t2g`, `md`, `mdst`, `cur` | P2-11 |
| `sec` | **Einsatzsekunden gesamt** | P1-8 |
| `g`, `a`, `y`, `r` | Tore, Assists, Gelbe, Rote | P1-8 |
| `cv` | „current value" (hier 33.600.000 bei `mv` 33.697.577) | — |
| `ismc`, `smc`, `smdc` | Einsatz-/Spieltagszähler | P1-8 |

### `GET /v4/leagues/{l}/players/{p}/performance`
`it[]` = Saisons (`sid`, `ti`, `n`), `it[].ph[]` = Spieltage:
`day, p (Punkte), mp ("90'"), pt, md, mdst, mi, t1, t2, t1g, t2g, st, cur, ap, asp, k[]`
→ Quelle für echte L5-Form und Minuten (P1-8, behebt D9).

### `GET /v4/leagues/{l}/ranking`
`us[]` je Manager: `i, n, sp (Saisonpunkte), mdp (Spieltagspunkte), spl (Platz), mdpl, tv,
hhmp, hhsp (Head-to-Head), lp[] (Spieler-IDs der Aufstellung), lipc, ppc, shp`
Root: `day, nd (34), lfmd, shmdn, gpm, ti, clpc` → P2-12.

### `GET /v4/competitions/1/table`
`it[]` je Verein: `tid, tn, cp (Punkte), cpl (Platz), pcpl (Vorplatz), mc (Spiele), gd, sp, mi`
→ FDR-Grundlage, seit P2-11 **gelesen** (`cpl`, `gd`, `mc`, `tid`, `tn`).

⚠️ Die Zeilen kommen **unsortiert**: die Response vom 23.09. beginnt mit Bayern (`cpl: 2`),
dann Stuttgart (`cpl: 15`), dann Elversberg (`cpl: 7`). Wer die Reihenfolge für die Tabelle
nimmt, liest den falschen Verein als Tabellenführer. `sp` ist die **Kickbase**-Punktausbeute des
Vereins (Bayern 9.500, Gladbach 2.325), nicht seine Spielstärke — für die Gegnerstärke zählt die
sportliche Tabelle.

### `GET /v4/competitions/1/matchdays` — der komplette Spielplan
Root: `day` (aktueller Spieltag), `it[]` = 34 Spieltags-Gruppen mit `day` und `it[]`.
Je Paarung: `mi, day, dt, t1, t2, t1sy, t2sy, st, il, t1im, t2im, fst`; gespielte zusätzlich
`t1g, t2g, mtd, mt`, kommende teils `bo` (Wettquoten).

`st == 2` heißt **beendet** — im Lauf vom 23.09. ausnahmslos: Spieltage 1–4 alle `st: 2` mit
Toren, Spieltage 5–34 alle `st: 0` ohne. 9 Paarungen je Spieltag, 306 insgesamt.

→ Seit P2-11 die Quelle des Restspielplans, **statt** `mdsum[]` aus `/players/{p}`: derselbe
Inhalt für einen Call statt für einen Call pro Spieler. Der Tick kennt den Endpunkt ohnehin als
Fallback für den Spieltagsstart (P0-1).

---

## 4. Antworten auf die offenen Fragen (§8 des Plans)

### F1 — Offers-Array: **offen, aber eingegrenzt**
Beide denkbaren GET-Endpunkte sind gesperrt (405, siehe §1). Das Array kann daher nur im
`/market`-Payload selbst liegen und wird erst sichtbar, wenn `ofc > 0` ist. Zusätzlich zeigt
`/leagues/{l}/squad` ein `ofc` je eigenem Spieler — das ist der billigere Trigger.
**Rest-Verfahren:** eigenen Spieler listen → warten bis `ofc > 0` → `scripts.inspect_endpoints`
erneut (schreibt dann `tmp/inspect/offers_found.json`).

> **[2026-09-26, P2-13] `ofc` hat zwei Bedeutungen — je nachdem, wessen Listing es ist.**
> Auf **eigenen** Listings sind es die eingegangenen Gebote fremder Manager (so wie oben für
> `/squad` beschrieben). Auf **fremden** Listings im `/market`-Payload zählt es dagegen nur die
> **eigenen** abgegebenen Gebote: Kickbase zeigt die Gebote anderer Manager nirgends an — der
> Hilfe-Artikel [„Warum habe ich den Spieler nicht bekommen?"](https://help.kickbase.com/help/ich-habe-auf-einen-spieler-geboten-warum-habe-ich-ihn-nicht-zum-transferzeitpunkt-bekommen)
> nennt als Kriterien nur höchstes bzw. frühestes Gebot, Kaderlimits und Marktwert zum
> Transferzeitpunkt, und kein Feld beziffert die Mitbieter. Der Plan führte `ofc` bis dahin als
> Konkurrenzmaß für die Overbid-Kalibrierung (P2-13) — das war falsch und ist korrigiert.

### F2 — `prob`-Richtung: **1 = sicherste Startelf** (starke Evidenz, App-Gegenprobe offen)
> Belegdatei: `docs/samples/market_prob_sample_2026-08-31.json`. Die Cassette vom 31.08. wurde
> in P0-0.3 durch eine aktuelle ersetzt, und Kickbase liefert `prob` außerhalb der
> Spieltagswoche nicht — ohne diese archivierte Stichprobe wäre die Antwort nicht mehr
> überprüfbar. `scripts/answer_open_questions.py` liest sie als dritte Quelle mit.

Median-Marktwert je Stufe (n=22):

| `prob` | n | Median-MW |
|---|---|---|
| 1 | 6 | 25.374.381 |
| 2 | 2 | 12.158.136 |
| 3 | 2 | 13.303.792 |
| 4 | 3 | 10.275.517 |
| 5 | 9 | 3.588.449 |

Stufe 1 enthält Guirassy/Baku/Maza, Stufe 5 den 500k-Ersatzkeeper Drewes sowie alle Spieler mit
`st != 0`. Die Delle bei 2/3 ist bei n=2 ohne Aussagekraft.

⚠️ **Wichtiger neuer Befund:** im Lauf vom 23.09. fehlt `prob` in **allen** 20 Market-Items.
Unterschied der beiden Läufe: am 31.08. war der nächste Spieltag 4 Tage entfernt, am 23.09.
16 Tage (Länderspielpause). **Arbeitshypothese:** Kickbase liefert `prob` erst, wenn
Startelf-Prognosen vorliegen — also in der Spieltagswoche. Ein Bot, der sich allein auf `prob`
stützt, steht außerhalb dieses Fensters ohne Daten da.
→ `sl` aus `/players/{p}` ist die robustere Primärquelle; `prob` bleibt der billigere
Massen-Indikator, wenn vorhanden. Entscheidung gehört in P0-3.

### F3 — `st`-Werte: **real beobachtet nur 0, 2, 4**
| `st` | Vorkommen | Quellen |
|---|---|---|
| 0 | 69 | alle Kader-/Markt-/Lineup-Dumps |
| 2 | 4 | market (live + Cassette) |
| 4 | 2 | market (live + Cassette) |

`PlayerStatus` kennt 0/1/2/4/8/16/32/64. `128` wurde **nicht** beobachtet — die Stichprobe ist
aber zu klein für eine abschließende Enum-Aussage (8 eigene Spieler, 42 Marktspieler, eine Liga).
**Konsequenz für P0-3:** die Frage ist nicht durch Datensammeln abschließbar. Der Defekt D6
(`_to_status` mappt Unbekanntes auf `FIT`) muss unabhängig davon behoben werden — unbekannte
Werte gehören auf einen expliziten `UNKNOWN`-Zustand plus `missing_data`-Flag, nie auf `FIT`.
`stl` (Status-Detailliste) war in allen Dumps leer.

### F4 — `mvud`: **beantwortet — der NÄCHSTE Update-Zeitpunkt**
| abgerufen | `mvud` | Lage |
|---|---|---|
| 2026-08-31T11:01:23Z | 2026-08-31T20:00:00Z | Zukunft (+9,0 h) |
| 2026-09-23T16:08:55Z | 2026-09-23T20:00:00Z | Zukunft (+3,9 h) |

Wäre `mvud` der *letzte* Zeitpunkt, müsste er am 23.09. um 16:08 Z auf den **22.09.** zeigen.
Tut er nicht. `20:00 UTC = 22:00 Europe/Berlin` deckt sich mit der dokumentierten Update-Zeit.
Restunsicherheit (welchen Wert zeigt er zwischen 20:00 und 24:00 UTC?) ist für die Fenster-Logik
in P1-10 irrelevant, solange der Scheduler `mvud` als Ziel nimmt und nach Ablauf neu lädt.
Protokoll wächst in `tmp/mvud_log.json` mit jedem Lauf.

### F5 — Laufzeit eigener Listings: **eigene Listings tragen kein `exs`**
Fremd-/Kickbase-Listings: 19 von 19 mit `exs`, Spanne **0,3 h – 43,7 h** (Plan §2.3 nannte
0,8–33 h; Spanne ist also weiter). Das eigene Listing (Spieler 1809, gelistet
2026-09-23T13:31:57Z, `prc` 9.200.000 bei `mv` 8.811.078) hat **kein** `exs`-Feld, dafür `dt`
= Listing-Zeitpunkt.
**Deutung:** ein eigenes Listing läuft nicht automatisch ab; es blockiert den Kaderplatz, bis es
angenommen oder per `DELETE /market/{pid}` zurückgezogen wird. Für den Prompt heißt das:
Listing = Plan A ohne Zeitdruck, aber ohne Zuschlagsgarantie; Sofortverkauf = garantierter Plan B.
**Rest-Verfahren:** dasselbe Listing nach > 72 h erneut prüfen.

---

## 5. Contract-Baseline

Die maschinell geprüfte Referenz der erwarteten Keys liegt in `docs/contract_baseline.json`
(14 Endpunkte, erzeugt aus dem Lauf vom 2026-09-23). Jeder Eintrag hat drei Listen:

| Liste | Bedeutung | Verletzung |
|---|---|---|
| `always` | Key steht in **jedem** Objekt an diesem Pfad | ✗ FEHLT → Exit 1 |
| `sometimes` | Key steht in einigen Objekten (z. B. `market.it[].u` nur bei User-Listings) | ✗ FEHLT, wenn er ganz verschwindet |
| `optional` | Key darf komplett fehlen — Pfad → Begründung | ~ toleriert, nie Alarm |

`optional` ist der Grund, warum der Checker nutzbar bleibt: `market.it[].prob` liefert Kickbase
nur in der Spieltagswoche (§4/F2). Ohne Ausnahmeliste würde jeder Lauf außerhalb dieses Fensters
rot melden — und ein Checker, der monatlich grundlos rot ist, wird ignoriert.

```bash
python -m scripts.inspect_endpoints                 # frische Payloads
python -m scripts.check_contract                    # Drift-Bericht (Exit 1 = Vertrag verletzt)
python -m scripts.check_contract --source cassettes # ohne Credentials, gegen die Cassettes
python -m scripts.check_contract --update           # Baseline bewusst nachziehen
```

**Monatlich laufen lassen.** Die `optional`-Einträge bleiben bei `--update` erhalten — sie sind
handgepflegtes Wissen, keine Messung.
