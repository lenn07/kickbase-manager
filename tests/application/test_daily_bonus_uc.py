"""Der tägliche Login-Bonus (P2-15).

Der Endpunkt ist der einzige im Projekt, dessen Verhalten **unbekannt** ist:
`GET /v4/bonus/collect` wurde bewusst nie abgerufen (§3.4), und ob ein zweiter
Aufruf am selben Tag harmlos ist, steht nirgends. Die Tests hier prüfen
deshalb vor allem die drei Sicherungen, die genau das abfangen — Kill-Switch,
`dry_run` und „höchstens einmal pro Kalendertag".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.application.daily_bonus_uc import BONUS_ACTION, CollectDailyBonusUseCase
from app.application.setup_service import SetupService, SmtpFormInput
from app.domain.exceptions import TransportError
from app.domain.models import LeagueMe
from app.infrastructure.crypto.vault import FernetVault
from app.infrastructure.persistence.models import TradeLogRow
from app.infrastructure.persistence.repositories import (
    SettingsRepository,
    TradeLogRepository,
    UserRepository,
)
from sqlmodel import Session

from tests.application.conftest import FakeKickbase, FakeLlm, FakeSmtp

# 09:00 Berlin = 07:00 UTC im Sommer — die Zeit, zu der der Job feuert.
NOW = datetime(2026, 9, 26, 7, 0, tzinfo=UTC)


class _BonusKickbase(FakeKickbase):
    """Zählt die Abrufe und lässt den Kontostand um den Bonus wachsen."""

    def __init__(self, *, bonus: int = 250_000, fail: bool = False, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._balance = Decimal(1_000_000)
        self._bonus = Decimal(bonus)
        self._fail = fail
        self.collect_calls = 0

    async def get_league_me(self, league_id: str) -> LeagueMe:
        return LeagueMe(league_id=league_id, budget=self._balance)

    async def collect_daily_bonus(self) -> dict[str, Any]:
        self.collect_calls += 1
        if self._fail:
            raise TransportError("bonus endpoint kaputt")
        self._balance += self._bonus
        # Erfundene Felder — der Punkt ist, dass sie **unverändert** ins
        # trade_log wandern, nicht dass der Code sie versteht.
        return {"unbekanntesFeld": 1, "irgendwas": "x"}


async def _setup(session: Session, vault: FernetVault, kb: FakeKickbase) -> int:
    service = SetupService(
        session=session, vault=vault, kickbase=kb, llm=FakeLlm(), smtp=FakeSmtp()
    )
    await service.verify_kickbase("user@example.com", "secret")
    service.select_league("L1")
    await service.verify_anthropic("sk-ant-good")
    await service.verify_smtp(
        SmtpFormInput(
            host="smtp.example.com",
            port=465,
            username="u",
            password="pw",
            from_addr="from@example.com",
            to_addr="to@example.com",
            use_tls=True,
            use_starttls=False,
        )
    )
    user = UserRepository(session).get_singleton()
    assert user is not None and user.id is not None
    return user.id


def _set_dry_run(session: Session, user_id: int, *, dry_run: bool) -> None:
    settings = SettingsRepository(session)
    row = settings.get_or_default(user_id)
    row.dry_run = dry_run
    settings.upsert(row)


async def test_disabled_switch_never_touches_the_endpoint(
    db_session: Session, vault: FernetVault
) -> None:
    """Der Default. Ein GET, der wie ein Write wirkt, geht nicht ohne Zutun raus."""
    kb = _BonusKickbase()
    await _setup(db_session, vault, kb)

    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=False).run(
        now=NOW
    )

    assert outcome.outcome == "disabled"
    assert kb.collect_calls == 0
    # Und keine Zeile: der Job läuft täglich, „ist aus" jeden Tag zu
    # protokollieren würde das trade_log fluten.
    assert TradeLogRepository(db_session).list_by_action(user_id=1, action=BONUS_ACTION) == []


async def test_dry_run_logs_but_does_not_collect(db_session: Session, vault: FernetVault) -> None:
    """Der Plan verlangt, den Job erst in `dry_run` zu beobachten.

    Dafür muss er sichtbar sein: keine HTTP-Abholung, aber eine Zeile im
    `trade_log`, an der man erkennt, dass er gelaufen wäre.
    """
    kb = _BonusKickbase()
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=True)

    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True).run(
        now=NOW
    )

    assert outcome.outcome == "dry_run"
    assert kb.collect_calls == 0
    rows = TradeLogRepository(db_session).list_by_action(user_id=user_id, action=BONUS_ACTION)
    assert len(rows) == 1
    assert rows[0].executed is False


async def test_collect_measures_the_bonus_on_the_balance(
    db_session: Session, vault: FernetVault
) -> None:
    """Die Höhe kommt aus dem Kontostand, nicht aus der Antwort.

    Die Felder des Endpunkts sind unbekannt; geraten wird nichts (§9). Was
    wirklich ankam, ist die Differenz vor und nach dem Call — und die
    Rohantwort steht daneben, damit der erste echte Lauf die Feldnamen liefert.
    """
    kb = _BonusKickbase(bonus=250_000)
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)

    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True).run(
        now=NOW
    )

    assert outcome.outcome == "collected"
    assert outcome.amount == Decimal(250_000)
    assert kb.collect_calls == 1
    row = TradeLogRepository(db_session).list_by_action(user_id=user_id, action=BONUS_ACTION)[0]
    assert row.executed is True
    assert row.price == 250_000
    assert row.context["response"] == {"unbekanntesFeld": 1, "irgendwas": "x"}


async def test_second_run_on_the_same_day_is_refused(
    db_session: Session, vault: FernetVault
) -> None:
    """Die Antwort auf die offene Idempotenz-Frage des Plans.

    Ob der Endpunkt einen zweiten Aufruf verträgt, weiß niemand — also kommt
    es nicht dazu. Ein Container-Neustart um 09:05 plant den Job neu, findet
    aber die Zeile von heute.
    """
    kb = _BonusKickbase()
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)
    uc = CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True)

    first = await uc.run(now=NOW)
    second = await uc.run(now=NOW + timedelta(hours=3))

    assert first.outcome == "collected"
    assert second.outcome == "already_today"
    assert kb.collect_calls == 1


async def test_the_day_boundary_follows_berlin_not_utc(
    db_session: Session, vault: FernetVault
) -> None:
    """Der Kickbase-Tag wechselt um Mitternacht **deutscher** Zeit.

    Ein Lauf um 23:30 Berlin (21:30 UTC) und einer um 00:30 Berlin sind zwei
    verschiedene Tage — nach UTC wäre es derselbe, und der zweite Bonus fiele
    aus.
    """
    kb = _BonusKickbase()
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)
    uc = CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True)

    late_evening = datetime(2026, 9, 26, 21, 30, tzinfo=UTC)  # 23:30 Berlin
    after_midnight = datetime(2026, 9, 26, 22, 30, tzinfo=UTC)  # 00:30 Berlin, neuer Tag

    assert (await uc.run(now=late_evening)).outcome == "collected"
    assert (await uc.run(now=after_midnight)).outcome == "collected"
    assert kb.collect_calls == 2


async def test_a_failed_attempt_still_blocks_the_day(
    db_session: Session, vault: FernetVault
) -> None:
    """Ein Fehler heißt nicht „nichts passiert".

    Der Endpunkt könnte gebucht und danach beim Antworten gescheitert sein.
    Ein zweiter Versuch am selben Tag würde das Risiko verdoppeln, ohne dass
    jemand hingesehen hat.
    """
    kb = _BonusKickbase(fail=True)
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)
    uc = CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True)

    first = await uc.run(now=NOW)
    second = await uc.run(now=NOW + timedelta(hours=2))

    assert first.outcome == "error"
    assert second.outcome == "already_today"
    assert kb.collect_calls == 1


async def test_streak_counts_consecutive_days_and_breaks_on_a_gap(
    db_session: Session, vault: FernetVault
) -> None:
    """Kickbase belohnt die ununterbrochene Serie.

    Sie ist zugleich die einzige Zahl, an der sich ablesen lässt, ob der Job
    zuverlässig läuft — ein ausgelassener Tag setzt sie zurück.
    """
    kb = _BonusKickbase()
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)
    trades = TradeLogRepository(db_session)
    # Gestern und vorgestern gesammelt, davor eine Lücke.
    for days_ago in (1, 2, 4):
        trades.add(
            TradeLogRow(
                user_id=user_id,
                ts=NOW - timedelta(days=days_ago),
                action=BONUS_ACTION,
                reason_text="Täglicher Login-Bonus (collected)",
                executed=True,
                context={"outcome": "collected"},
            )
        )

    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True).run(
        now=NOW
    )

    assert outcome.outcome == "collected"
    assert outcome.streak == 3  # gestern, vorgestern, heute — der Tag davor fehlt


async def test_dry_run_does_not_keep_the_streak_alive(
    db_session: Session, vault: FernetVault
) -> None:
    """Nur echte Abholungen zählen — ein Dry-Run hat den Bonus nicht geholt."""
    kb = _BonusKickbase()
    user_id = await _setup(db_session, vault, kb)
    _set_dry_run(db_session, user_id, dry_run=False)
    TradeLogRepository(db_session).add(
        TradeLogRow(
            user_id=user_id,
            ts=NOW - timedelta(days=1),
            action=BONUS_ACTION,
            reason_text="Täglicher Login-Bonus (dry_run)",
            executed=False,
            context={"outcome": "dry_run"},
        )
    )

    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True).run(
        now=NOW
    )

    assert outcome.streak == 1


async def test_incomplete_setup_is_skipped(db_session: Session, vault: FernetVault) -> None:
    kb = _BonusKickbase()
    outcome = await CollectDailyBonusUseCase(session=db_session, kickbase=kb, enabled=True).run(
        now=NOW
    )
    assert outcome.outcome == "skipped_setup"
    assert kb.collect_calls == 0
