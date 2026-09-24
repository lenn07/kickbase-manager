"""Wann der Bot aufwachen muss — reine Zeitrechnung, ohne Scheduler (P1-10).

Der feste 120-Minuten-Takt verschenkt fast alles: rund elf von zwölf Ticks
fallen in Stunden, in denen sich nichts bewegt, und der eine, der zählt, trifft
den Zeitpunkt nur zufällig. Defekt D12.

Der Kalender aus §2.6 des Optimizing-Plans nennt fünf Momente, und sie zerfallen
in zwei Arten:

**Feste Uhrzeiten** — sie hängen am Marktwert-Update (täglich 22:00 Berlin) und
am Punkte-Abschluss:

| Fenster | Warum |
|---|---|
| 21:45 | letzte Gelegenheit vor dem Marktwert-Update: Gebote platzieren, Listings prüfen |
| 22:15 | direkt danach: Gewinner und Verlierer auswerten, Positionen drehen |
| Mo 18:30 | finale Spieltagspunkte und Prämien stehen, Kader-Review |

**Bewegliche Zeitpunkte** — sie hängen am Anpfiff und müssen **relativ** zu
`next_matchday_start` gerechnet werden, nicht auf Freitag festgenagelt:
englische Wochen pfeifen dienstags um 18:30 an.

| Fenster | Warum |
|---|---|
| Anpfiff minus 45 min | Konto muss ins Plus, Startelf muss stehen — danach ist beides eingefroren |
| Anpfiff + 5 min | das Minus-Fenster öffnet sich, bis 33 % darf gekauft werden |

Die Zeitzone ist hier nicht kosmetisch: 21:45 heißt **21:45 in Berlin**, und der
Abstand zu UTC wechselt zweimal im Jahr. Wer in UTC rechnet, liegt im Winter
eine Stunde daneben — also genau an den Spieltagen, an denen der Marktwert
zwischen Hin- und Rückrunde am meisten schwankt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

# Feste Fenster als (Name, Stunde, Minute) in **lokaler** Zeit.
DAILY_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("pre_market_value_update", 21, 45),
    ("post_market_value_update", 22, 15),
)

# Wochen-Fenster: (Name, Wochentag als Cron-Kürzel, Stunde, Minute).
WEEKLY_WINDOWS: tuple[tuple[str, str, int, int], ...] = (("matchday_review", "mon", 18, 30),)

# Abstände zum Anpfiff.
DEADLINE_LEAD = timedelta(minutes=45)
DEBT_WINDOW_LAG = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class MatchdayWindow:
    """Ein einmaliger Weckruf, an den Anpfiff gekoppelt."""

    name: str
    at: datetime


def matchday_windows(next_matchday_start: datetime | None) -> tuple[MatchdayWindow, ...]:
    """Die beweglichen Fenster rund um den nächsten Anpfiff.

    Beide werden **aus** `next_matchday_start` gerechnet. Der Plan schreibt das
    ausdrücklich so, und der Grund ist die englische Woche: ein hart auf
    Freitag 20:30 verdrahtetes Deadline-Fenster verpasst den Dienstagsanpfiff
    um 18:30 komplett — und das ist der Spieltag, an dem ein negatives Konto
    dieselben 0 Punkte kostet.

    Ohne bekannten Anpfiff gibt es nichts zu planen: dann bleibt es beim
    Intervall-Fallback, statt einen Zeitpunkt zu erfinden.
    """
    if next_matchday_start is None:
        return ()
    return (
        MatchdayWindow(name="pre_deadline", at=next_matchday_start - DEADLINE_LEAD),
        MatchdayWindow(name="debt_window_open", at=next_matchday_start + DEBT_WINDOW_LAG),
    )


def upcoming_matchday_windows(
    next_matchday_start: datetime | None, *, now: datetime
) -> tuple[MatchdayWindow, ...]:
    """Wie `matchday_windows`, aber ohne die bereits verstrichenen.

    APScheduler feuert einen `DateTrigger` in der Vergangenheit nicht — er
    würde den Job stillschweigend als erledigt verwerfen. Das Herausfiltern
    hier macht sichtbar, was sonst nur als fehlender Job auffiele.
    """
    return tuple(w for w in matchday_windows(next_matchday_start) if w.at > now)
