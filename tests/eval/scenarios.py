"""Feste Entscheidungs-Szenarien für die Prompt-Eval (Optimizing-Plan §5, Stufe 4).

Bewusst **synthetisch** statt aus den Cassettes: eine Eval misst, ob der
Master-Prompt eine Regel befolgt. Dafür muss genau ein Faktor variieren — mit
echten Marktdaten weiß man hinterher nicht, ob die Regel gegriffen hat oder die
Lage zufällig eindeutig war.

Jedes Szenario nennt **erlaubte** und **verbotene** Aktionen statt einer einzigen
erwarteten. Ein LLM darf zwischen „jetzt verkaufen" und „noch halten" schwanken;
es darf nicht gegen eine harte Kickbase-Regel verstoßen. Genau das prüfen wir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from app.application.decision_engine import DecisionContext, OpenBid
from app.application.player_enrichment import PlayerEnrichment
from app.domain.lineup import DEFAULT_FORMATION, LINEUP_SIZE, Lineup
from app.domain.models import (
    LeagueConstraints,
    LeagueMe,
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction

NOW = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
LEAGUE_ID = "1111111"
MANAGER_ID = "9999999"


@dataclass(frozen=True, slots=True)
class Scenario:
    """Eine Lage + die Aktionen, die der Master-Prompt zulassen darf."""

    name: str
    description: str
    context: DecisionContext
    allowed: frozenset[TradeAction]
    forbidden: frozenset[TradeAction] = field(default_factory=frozenset)
    # Spieler, die das Modell nicht anfassen darf. Manche Regeln verbieten
    # keine Aktionsart, sondern eine Auswahl: „kaufen ist in Ordnung, **den**
    # zu kaufen nicht". Ohne dieses Feld liesse sich das nur als Verbot der
    # ganzen Aktion formulieren — und das prüfte dann die falsche Regel.
    forbidden_player_ids: frozenset[str] = field(default_factory=frozenset)
    # Untergrenze für ein Gebot, als Anteil am Marktwert des gewählten
    # Spielers. `None` = keine Prüfung. Manche Regeln betreffen weder die
    # Aktionsart noch die Auswahl, sondern allein die Höhe.
    min_bid_ratio: float | None = None
    # Was die Regel im Prompt ist, gegen die hier geprüft wird.
    rule: str = ""
    # Normalerweise steht eine vollständige Elf, sonst prüfte jedes Szenario
    # nebenbei die -100-Regel statt der gemeinten. Ein Szenario, das **über**
    # die Aufstellung geht, setzt das bewusst auf False.
    expects_full_lineup: bool = True


def _player(
    pid: str,
    name: str,
    position: Position,
    market_value: int,
    *,
    status: PlayerStatus = PlayerStatus.FIT,
    average_points: float = 120.0,
) -> Player:
    return Player(
        id=pid,
        first_name="",
        last_name=name,
        team_id="2",
        position=position,
        status=status,
        market_value=Decimal(market_value),
        average_points=average_points,
        total_points=int(average_points * 4),
    )


def _enrichment(
    player: Player,
    *,
    trend_7d: float | None = 1.5,
    trend_1d: float | None = 0.2,
    start_probability: float = 0.85,
    minutes_last5: float | None = 88.0,
    starts_last5: int | None = 5,
) -> PlayerEnrichment:
    return PlayerEnrichment(
        player_id=player.id,
        market_trend_1d_pct=trend_1d,
        market_trend_3d_pct=trend_7d,
        market_trend_7d_pct=trend_7d,
        market_trend_30d_pct=trend_7d,
        mv_max_30d=int(player.market_value),
        avg_points_last5=player.average_points,
        start_probability_next=start_probability,
        # Die Szenarien sollen den Prompt gegen eine Regel prüfen, nicht gegen
        # eine Datenlücke — deshalb überall die beste Quelle.
        start_probability_source="kickbase_prob",
        injury_status="fit" if player.status is PlayerStatus.FIT else "injured",
        missing_data_flags=(),
        # Default: ein durchspielender Stammspieler. Ein Szenario, das über
        # Rotation geht, setzt die beiden Werte ausdrücklich herunter (P1-8).
        minutes_last5=minutes_last5,
        starts_last5=starts_last5,
        form_matchdays_counted=5,
    )


def _context(
    *,
    squad_players: list[Player],
    market_players: list[Player],
    cash: int,
    team_value: int,
    minutes_until_matchday: int,
    enrichment_overrides: dict[str, PlayerEnrichment] | None = None,
    placed_in_lineup: int | None = None,
    market_offer_counts: dict[str, int] | None = None,
    market_expiry_s: int = 6 * 3600,
    buy_prices: dict[str, int] | None = None,
    squad_limit: int | None = 16,
    open_bids: dict[str, int] | None = None,
) -> DecisionContext:
    """Baut eine Lage. `placed_in_lineup` steuert, wie viele Slots besetzt sind.

    Default ist eine vollständige Elf — sonst prüfte jedes Szenario nebenbei die
    -100-Regel statt der Regel, um die es eigentlich geht.
    """
    placed = LINEUP_SIZE if placed_in_lineup is None else placed_in_lineup
    entry_prices = buy_prices or {}
    squad = Squad(
        league_id=LEAGUE_ID,
        manager_id=MANAGER_ID,
        players=tuple(
            SquadPlayer(
                player=p,
                lineup_order=i if i < placed else None,
                # Kickbase liefert den Einstand für jeden Kaderspieler (P1-6).
                # Ohne ihn sähe jede Eval-Lage aus wie ein Spieler, über dessen
                # Kaufpreis nichts bekannt ist — der Ausnahmefall, nicht der
                # Normalfall.
                buy_price=Decimal(entry_prices.get(p.id, int(p.market_value))),
                unrealized_pnl=p.market_value
                - Decimal(entry_prices.get(p.id, int(p.market_value))),
            )
            for i, p in enumerate(squad_players)
        ),
    )
    offer_counts = market_offer_counts or {}
    market = tuple(
        MarketPlayer(
            player=p,
            price=p.market_value,
            expires_in_s=market_expiry_s,
            seller_id=None,
            offer_count=offer_counts.get(p.id, 0),
            offers=(),
        )
        for p in market_players
    )
    enrichment = {p.id: _enrichment(p) for p in squad_players + market_players}
    enrichment.update(enrichment_overrides or {})

    # 33 %-Regel, wie `run_tick_uc._max_negative_allowed` sie rechnet.
    basis = Decimal(team_value) + min(Decimal(0), Decimal(cash))
    max_negative = -(basis * Decimal("0.33")).quantize(Decimal(1))

    return DecisionContext(
        league_id=LEAGUE_ID,
        league_me=LeagueMe(league_id=LEAGUE_ID, budget=Decimal(cash)),
        squad=squad,
        market=market,
        budget=Decimal(cash),
        min_action_score=0.6,
        max_trade_pct=0.25,
        min_cash_reserve=0,
        team_value=Decimal(team_value),
        open_bids_total=sum((Decimal(p) for p in (open_bids or {}).values()), Decimal(0)),
        open_bids={
            pid: OpenBid(player_id=pid, price=Decimal(price), placed_at=NOW - timedelta(hours=3))
            for pid, price in (open_bids or {}).items()
        },
        now=NOW,
        next_matchday_start=NOW + timedelta(minutes=minutes_until_matchday),
        interval_min=120,
        enrichment=enrichment,
        max_negative_allowed=max_negative,
        current_balance_after_open_bids=Decimal(cash)
        - sum((Decimal(p) for p in (open_bids or {}).values()), Decimal(0)),
        lineup=Lineup(
            formation=DEFAULT_FORMATION,
            player_ids=tuple(p.id for p in squad_players[:placed]),
        ),
        lineup_deadline=NOW + timedelta(minutes=minutes_until_matchday),
        # Die echte Liga (§8/F6, am 2026-09-24 abgelesen): kein Vereinslimit,
        # Unterbieten deaktiviert, Wertung nach Saisonpunkten. Die Eval misst
        # damit dieselbe Lage, in der der Bot produktiv entscheidet — mit
        # „unbekannt" überall prüfte sie einen Zustand, den es nicht gibt.
        constraints=LeagueConstraints(
            squad_limit=squad_limit,
            club_limit=None,
            club_limit_is_unlimited=True,
            players_per_club=_players_per_club(squad_players),
            underpay_blocked=True,
            scoring_mode="season_points",
        ),
    )


def _players_per_club(players: list[Player]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in players:
        counts[p.team_id] = counts.get(p.team_id, 0) + 1
    return counts


def _squad_of_twelve() -> list[Player]:
    """Elf Startelf-Spieler plus einer auf der Bank, in gültiger Verteilung."""
    return [
        _player("101", "Keeper", Position.GOALKEEPER, 8_000_000),
        *[_player(f"20{i}", f"Abwehr{i}", Position.DEFENDER, 12_000_000) for i in range(1, 5)],
        *[
            _player(f"30{i}", f"Mittelfeld{i}", Position.MIDFIELDER, 15_000_000)
            for i in range(1, 5)
        ],
        *[_player(f"40{i}", f"Sturm{i}", Position.FORWARD, 18_000_000) for i in range(1, 3)],
        _player("501", "Bankspieler", Position.DEFENDER, 5_000_000, average_points=40.0),
    ]


def _squad_of_fourteen() -> list[Player]:
    """Elf Aufgestellte plus drei fitte Ersatzspieler — für das SET_LINEUP-Szenario."""
    return [
        *_squad_of_twelve(),
        _player("502", "Ersatz-Mittelfeld", Position.MIDFIELDER, 6_000_000, average_points=95.0),
        _player("503", "Ersatz-Sturm", Position.FORWARD, 7_000_000, average_points=105.0),
    ]


def _debt_before_kickoff() -> Scenario:
    """Konto im Minus, Anpfiff in 90 Minuten.

    Härteste Regel des Spiels: negatives Konto beim Anpfiff = 0 Punkte für den
    ganzen Spieltag. Der Bot muss Geld beschaffen, nicht ausgeben.
    """
    squad = _squad_of_twelve()
    market = [_player("901", "Schnaeppchen", Position.MIDFIELDER, 6_000_000)]
    return Scenario(
        name="debt_before_kickoff",
        description="Konto -4,2 Mio, Anpfiff in 90 min",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=-4_200_000,
            team_value=150_000_000,
            minutes_until_matchday=90,
        ),
        allowed=frozenset({TradeAction.SELL, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.BUY, TradeAction.HOLD}),
        rule="Konto muss zum Anpfiff >= 0 sein, sonst 0 Punkte (Regelwerk §2.2.1)",
    )


def _healthy_and_quiet() -> Scenario:
    """Konto im Plus, Kader vollständig, Anpfiff in vier Tagen, Markt unauffällig.

    Hier ist Nichtstun eine legitime Antwort — ein Bot, der jeden Tick handeln
    muss, verbrennt Marge. Verboten ist nur der Panikverkauf.
    """
    squad = _squad_of_twelve()
    market = [_player("902", "Durchschnitt", Position.DEFENDER, 9_000_000, average_points=60.0)]
    return Scenario(
        name="healthy_and_quiet",
        description="Konto +8 Mio, Kader voll, Anpfiff in 4 Tagen",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=8_000_000,
            team_value=150_000_000,
            minutes_until_matchday=4 * 24 * 60,
        ),
        allowed=frozenset({TradeAction.HOLD, TradeAction.BUY, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.SELL}),
        rule="Sofortverkauf ohne Kapitalbedarf verschenkt den Listing-Aufschlag (§2.3)",
    )


def _injured_starter() -> Scenario:
    """Teuerster Stammspieler langzeitverletzt, Marktwert dreht nach unten.

    Verkaufsgrund 2 aus dem Regelwerk: die These ist gebrochen. Ein Kauf wäre
    hier die falsche Reaktion — das Kapital steckt im falschen Spieler.

    **`SET_LINEUP` gehört seit dem Eval-Lauf vom 2026-09-24 dazu.** Das
    Szenario stammt aus P0-0.6, also aus der Zeit vor der Aufstellungs-Aktion,
    und wurde bei deren Einführung nicht nachgezogen. Das Modell wählte
    dreimal einstimmig `SET_LINEUP` mit der Begründung, der Verletzte (5 %
    Startelf-Chance) stehe in der Elf, während ein fitter Bankspieler daneben
    sitze — und das ist bei drei Tagen bis zum Anpfiff die bessere Aktion als
    ein Verkauf: sie wirkt sofort auf die Punkte, ist umkehrbar und verbrennt
    kein Kapital zum schlechtesten Zeitpunkt.

    Die Regel, um die es hier geht, ist damit **nicht** verletzt: geprüft wird,
    dass der Bot nicht *nachkauft*, während sein Kapital im falschen Spieler
    steckt. `BUY` bleibt deshalb erlaubt (der Ersatz am Markt ist legitim),
    `ACCEPT_OFFER`/`DECLINE_OFFER` bleiben es nicht — es gibt keine Gebote.
    """
    squad = _squad_of_twelve()
    injured = _player(
        "301", "Mittelfeld1", Position.MIDFIELDER, 15_000_000, status=PlayerStatus.INJURED
    )
    squad[5] = injured
    market = [_player("903", "Ersatz", Position.MIDFIELDER, 7_000_000)]
    overrides = {
        injured.id: _enrichment(injured, trend_7d=-4.5, trend_1d=-1.2, start_probability=0.05)
    }
    return Scenario(
        name="injured_starter",
        description="Stammspieler verletzt, 7-Tage-Trend -4,5 %",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=2_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
            enrichment_overrides=overrides,
        ),
        allowed=frozenset(
            {
                TradeAction.SELL,
                TradeAction.LIST_ON_MARKET,
                TradeAction.HOLD,
                TradeAction.BUY,
                TradeAction.SET_LINEUP,
            }
        ),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        rule=(
            "These gebrochen (Verletzung) ist Verkaufsgrund 2 (§2.6); ihn aus der Elf zu "
            "nehmen ist die schnellere Antwort auf dasselbe Problem"
        ),
    )


# -- Szenarien zu den Regeln, die P0-5 neu in den Prompt gebracht hat ------


def _open_lineup_slots() -> Scenario:
    """Zehn aufgestellt, drei fitte auf der Bank, drei Stunden bis Anpfiff.

    Der leere Slot kostet 100 Punkte, die Ersatzspieler kosten nichts. Es gibt
    keinen Grund, ihn offen zu lassen — und der Prompt kennt seit P0-5 die
    Aktion, um ihn zu schliessen.
    """
    squad = _squad_of_fourteen()
    market = [_player("904", "Irgendwer", Position.MIDFIELDER, 8_000_000, average_points=70.0)]
    return Scenario(
        name="open_lineup_slots",
        description="10 von 11 Slots besetzt, 3 fitte Ersatzspieler, Anpfiff in 3 h",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=3_000_000,
            team_value=150_000_000,
            minutes_until_matchday=180,
            placed_in_lineup=10,
        ),
        allowed=frozenset({TradeAction.SET_LINEUP}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL}),
        rule="Jede unbesetzte Startelf-Position kostet -100 Punkte (Regelwerk §2.2.2)",
        expects_full_lineup=False,
    )


def _instant_sale_is_not_a_discount() -> Scenario:
    """Konto tief im Minus, 40 Minuten bis Anpfiff, kein Listing läuft.

    Der alte Prompt behauptete, `SELL_INSTANT` liege „meist unter Marktwert" —
    tatsächlich bringt er den vollen Marktwert. Ein Modell, das den Abschlag
    fürchtet, listet stattdessen und verpasst die Deadline: ein Listing hat
    keine Zuschlagsgarantie, der Sofortverkauf schon.
    """
    squad = _squad_of_twelve()
    market = [_player("905", "Verlockung", Position.FORWARD, 9_000_000)]
    return Scenario(
        name="instant_sale_before_deadline",
        description="Konto -6 Mio, Anpfiff in 40 min, kein laufendes Listing",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=-6_000_000,
            team_value=150_000_000,
            minutes_until_matchday=40,
        ),
        allowed=frozenset({TradeAction.SELL}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.BUY}),
        rule=(
            "Sofortverkauf bringt den vollen Marktwert und ist der garantierte Plan B, "
            "wenn das Konto bis zum Anpfiff ins Plus muss (Regelwerk §2.2.1/§2.3)"
        ),
    )


def _bench_player_is_no_bargain() -> Scenario:
    """Hoher Saison-Ø, aber die Startelf-Prognose sagt: spielt nicht.

    Minuten sind die Basis von allem. Ein Spieler mit 140 Punkten Schnitt und
    5 % Startelf-Chance bringt im Erwartungswert 7 — weniger als jeder
    Stammspieler im Kader.
    """
    squad = _squad_of_twelve()
    bench_star = _player("906", "Bankstar", Position.FORWARD, 14_000_000, average_points=140.0)
    market = [bench_star]
    overrides = {bench_star.id: _enrichment(bench_star, start_probability=0.05)}
    return Scenario(
        name="bench_player_is_no_bargain",
        description="Marktspieler mit 140 Punkten Schnitt, Startelf-Chance 5 %",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=20_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
            enrichment_overrides=overrides,
        ),
        allowed=frozenset({TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.BUY}),
        rule="Startelf-Wahrscheinlichkeit schlägt Form — wer nicht spielt, punktet nicht (§2.4)",
    )


def _profit_peak() -> Scenario:
    """7-Tage-Trend stark positiv, 1-Tage-Trend dreht, Marktwert am 30-Tage-Hoch.

    Verkaufsgrund 1 des Regelwerks: der Peak. Kaufen wäre hier die Umkehrung
    des Signals — die Nachfrage ist bereits eingepreist.
    """
    squad = _squad_of_twelve()
    peaked = squad[9]  # ein Stürmer aus der Startelf
    market = [_player("907", "Neutral", Position.DEFENDER, 8_000_000, average_points=80.0)]
    overrides = {
        peaked.id: _enrichment(peaked, trend_7d=18.0, trend_1d=-1.2, start_probability=0.9)
    }
    return Scenario(
        name="profit_peak",
        description="Kaderspieler +18 % in 7 d, 1-d-Trend -1,2 %, MW am 30-d-Hoch",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=2_000_000,
            team_value=150_000_000,
            minutes_until_matchday=4 * 24 * 60,
            enrichment_overrides=overrides,
        ),
        allowed=frozenset({TradeAction.LIST_ON_MARKET, TradeAction.SELL, TradeAction.HOLD}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        rule=(
            "Peak = 7-d-Trend positiv, 1-d-Trend dreht, MW nahe mv_max_30d — "
            "Verkaufsgrund 1 (§2.6). Ohne eingehendes Gebot ist ACCEPT/DECLINE unmöglich."
        ),
    )


def _no_offers_means_no_accept() -> Scenario:
    """`incoming_offers` ist leer — dann gibt es nichts anzunehmen.

    Der Code verwirft eine `offer_id`, die nicht im Kontext steht, und der Tick
    ist verloren. Das Szenario prüft, ob der Prompt das verhindert, statt sich
    auf die Code-Sperre zu verlassen.
    """
    squad = _squad_of_twelve()
    market = [_player("908", "Beliebig", Position.MIDFIELDER, 7_000_000, average_points=85.0)]
    return Scenario(
        name="no_offers_means_no_accept",
        description="Kader vollständig, Konto im Plus, keine eingehenden Gebote",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=5_000_000,
            team_value=150_000_000,
            minutes_until_matchday=2 * 24 * 60,
        ),
        allowed=frozenset(
            {TradeAction.HOLD, TradeAction.BUY, TradeAction.LIST_ON_MARKET, TradeAction.SELL}
        ),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        rule=(
            "IDs nur aus dem Kontext - leeres `incoming_offers` heisst: "
            "die Aktion ist nicht verfügbar (§6)"
        ),
    )


def _joker_is_no_starter() -> Scenario:
    """Gleiche Punkte, andere Minuten — der Fall, für den P1-8 gebaut wurde.

    Zwei Marktspieler mit identischem Saison-Schnitt und identischer Form:
    der eine spielt durch (88 min, 5 Startelf-Einsätze), der andere kommt
    zweimal für zwölf Minuten (starts_last5 = 0). Vor P1-8 standen im Payload
    nur die Punkte — beide sahen exakt gleich aus, und ein Kauf des Jokers war
    aus Sicht des Modells nicht von einem Kauf des Stammspielers zu
    unterscheiden.

    Geprüft wird nicht „kaufe niemanden", sondern: **wenn** gekauft wird, dann
    nicht der Joker. Dafür trägt die Begründung des Modells die Last — die
    Assertion prüft, dass der Joker nicht die gewählte `player_id` ist.
    """
    squad = _squad_of_twelve()
    starter = _player("910", "Durchspieler", Position.MIDFIELDER, 9_000_000, average_points=130.0)
    joker = _player("911", "Einwechsler", Position.MIDFIELDER, 9_000_000, average_points=130.0)
    overrides = {
        starter.id: _enrichment(starter, minutes_last5=88.0, starts_last5=5),
        # Dieselben Punkte aus einem Fünftel der Spielzeit: ein Joker mit
        # Rotationsrisiko, kein Stammspieler.
        joker.id: _enrichment(joker, minutes_last5=12.0, starts_last5=0, start_probability=0.30),
    }
    return Scenario(
        name="joker_is_no_starter",
        description=(
            "Zwei Marktspieler, gleiche Punkte — einer spielt durch, einer wird eingewechselt"
        ),
        context=_context(
            squad_players=squad,
            market_players=[starter, joker],
            cash=20_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
            enrichment_overrides=overrides,
        ),
        allowed=frozenset({TradeAction.BUY, TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        forbidden_player_ids=frozenset({joker.id}),
        rule=(
            "Minuten sind die Basis von allem — `starts_last5` 0 bei gleichem Punkteschnitt "
            "heisst Rotationsrisiko, nicht Schnaeppchen (§1.2)"
        ),
    )


def _squad_is_full() -> Scenario:
    """Kaderlimit erreicht — ein Kauf ist regelwidrig, nicht nur unklug.

    Vor P1-9 stand das Limit nicht im Payload; das Modell konnte gar nicht
    wissen, dass der Kader voll ist. Jetzt sagt `constraints.squad_slots_left`
    es ausdrücklich, und ein BUY wäre ein verbrannter Tick: Kickbase lehnt das
    Gebot ab.

    Geld ist reichlich da und ein attraktiver Spieler liegt am Markt — genau
    die Lage, in der die Regel gegen den Anreiz stehen muss.
    """
    squad = [
        *_squad_of_twelve(),
        _player("601", "Reserve1", Position.DEFENDER, 4_000_000, average_points=50.0),
        _player("602", "Reserve2", Position.MIDFIELDER, 4_000_000, average_points=55.0),
    ]
    bargain = _player("920", "Sehr gut", Position.FORWARD, 12_000_000, average_points=175.0)
    return Scenario(
        name="squad_is_full",
        description="14 Spieler bei Kaderlimit 14, viel Cash, attraktiver Spieler am Markt",
        context=_context(
            squad_players=squad,
            market_players=[bargain],
            cash=40_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
            squad_limit=len(squad),
        ),
        allowed=frozenset({TradeAction.HOLD, TradeAction.LIST_ON_MARKET, TradeAction.SELL}),
        forbidden=frozenset({TradeAction.BUY}),
        rule=(
            "Kaderlimit ist eine Liga-Einstellung und steht als "
            "`constraints.squad_slots_left` im Kontext — bei 0 ist BUY nicht möglich (§1.1)"
        ),
    )


def _underpay_is_blocked() -> Scenario:
    """Unterbieten ist in dieser Liga abgeschaltet — jedes Gebot < Marktwert scheitert.

    Der Marktspieler ist fair bewertet und der Kader hat Platz; der Anreiz,
    ein paar Prozent zu sparen, ist also da. Er darf nicht dazu führen, dass
    der Bot unter Marktwert bietet: Kickbase lehnt das Gebot nicht erst beim
    Transferzeitpunkt ab, sondern lässt es gar nicht erst zu — der Tick ist
    weg, ohne dass irgendetwas passiert ist.

    Geprüft wird über den Preis, nicht über die Aktionsart: BUY ist hier
    völlig in Ordnung, nur eben nicht zu 92 % des Marktwerts.
    """
    squad = _squad_of_twelve()
    target = _player("930", "Fair bewertet", Position.MIDFIELDER, 10_000_000, average_points=140.0)
    return Scenario(
        name="underpay_is_blocked",
        description="Unterbieten deaktiviert, Kader hat Platz, fair bewerteter Spieler am Markt",
        context=_context(
            squad_players=squad,
            market_players=[target],
            cash=25_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
        ),
        allowed=frozenset({TradeAction.BUY, TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        # Bietet das Modell, muss das Gebot mindestens den Marktwert treffen.
        min_bid_ratio=1.0,
        rule=(
            "`constraints.underpay_blocked: true` — jedes Gebot unter Marktwert ist "
            "blockiert, nicht erst eins unter Marktwert minus 10 Prozent (§3)"
        ),
    )


def _bid_already_running() -> Scenario:
    """Auf diesen Spieler läuft schon ein eigenes Gebot — nicht noch einmal bieten.

    Der Fall aus dem Betrieb am 2026-09-24: der Bot bot siebenmal hintereinander
    denselben Betrag auf denselben Spieler, weil `open_bids_total` konstant 0
    war und er sein eigenes Gebot nirgends sah (Defekt D3).

    Die Lage ist bewusst verlockend: Kaderlücken, Geld da, ein guter Spieler am
    Markt. Genau dann muss `my_open_bid_price` die Wiederholung verhindern.
    `offer_count` steht auf 1 — das ist unser eigenes Gebot, es bietet also
    niemand dagegen, und Erhöhen wäre Bieten gegen sich selbst.

    `BUY` bleibt als Aktion erlaubt: es gibt einen **zweiten** Marktspieler
    ohne laufendes Gebot, und ihn zu kaufen ist völlig richtig. Verboten ist
    allein die Wiederholung auf den ersten.
    """
    squad = _squad_of_twelve()
    running = _player("940", "Läuft schon", Position.MIDFIELDER, 11_000_000, average_points=150.0)
    free = _player("941", "Noch frei", Position.DEFENDER, 8_000_000, average_points=130.0)
    return Scenario(
        name="bid_already_running",
        description="Eigenes Gebot über 11 Mio läuft, offer_count=1, Kader hat Platz",
        context=_context(
            squad_players=squad,
            market_players=[running, free],
            cash=30_000_000,
            team_value=150_000_000,
            minutes_until_matchday=3 * 24 * 60,
            market_offer_counts={running.id: 1},
            open_bids={running.id: 11_000_000},
        ),
        allowed=frozenset({TradeAction.BUY, TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        forbidden_player_ids=frozenset({running.id}),
        rule=(
            "Ein Gebot ist kein Kauf: es läuft bis zum Listing-Ablauf. Bei "
            "`my_open_bid_price != null` und `offer_count == 1` bietet man nicht gegen "
            "sich selbst (§3)"
        ),
    )


def _squad_too_small_to_field_eleven() -> Scenario:
    """Sieben Spieler, vier leere Startelf-Slots — die reale Lage am 2026-09-24.

    Das ist der einzige Zustand im Spiel, in dem **Nichtstun** garantiert Punkte
    kostet: jeder unbesetzte Slot ist -100 pro Spieltag, hier also -400. Und er
    ist mit `SET_LINEUP` **nicht** lösbar — im Kader sind schlicht keine elf
    Spieler. Nur ein Kauf hilft.

    Das Szenario ist der Gegenpol zu allen anderen: sie prüfen, dass der Bot
    sich zurückhält, wo Zurückhaltung richtig ist. Hier ist sie falsch, und
    `HOLD` steht deshalb in `forbidden`. Ohne so ein Szenario wird jede
    Verschärfung des Prompts „grün", weil HOLD überall erlaubt ist — die Eval
    misst dann nur noch, dass der Bot nichts tut.

    Geld ist reichlich da, Marktspieler sind verfügbar und ihre Listings laufen
    **vor** dem Anpfiff ab. Es gibt also keinen Grund zu warten.
    """
    squad = [
        _player("101", "Keeper", Position.GOALKEEPER, 8_000_000),
        *[_player(f"20{i}", f"Abwehr{i}", Position.DEFENDER, 12_000_000) for i in range(1, 4)],
        *[
            _player(f"30{i}", f"Mittelfeld{i}", Position.MIDFIELDER, 15_000_000)
            for i in range(1, 3)
        ],
        _player("401", "Sturm1", Position.FORWARD, 18_000_000),
    ]
    market = [
        _player("950", "Verteidiger frei", Position.DEFENDER, 9_000_000, average_points=110.0),
        _player("951", "Mittelfeld frei", Position.MIDFIELDER, 7_500_000, average_points=125.0),
        _player("952", "Stuermer frei", Position.FORWARD, 6_000_000, average_points=95.0),
    ]
    return Scenario(
        name="squad_too_small_to_field_eleven",
        description="7 Spieler im Kader, 4 leere Startelf-Slots (400 Punkte Risiko), 35 Mio Cash",
        context=_context(
            squad_players=squad,
            market_players=market,
            cash=35_000_000,
            team_value=90_000_000,
            minutes_until_matchday=2 * 24 * 60,
            placed_in_lineup=len(squad),
            # Alle Listings laufen lange vor dem Anpfiff ab — ein Gebot kommt
            # rechtzeitig zum Zuschlag, das Warten hat also keinen Grund.
            market_expiry_s=12 * 3600,
        ),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL, TradeAction.ACCEPT_OFFER}),
        expects_full_lineup=False,
        rule=(
            "Jeder leere Startelf-Slot kostet 100 Punkte pro Spieltag und ist mit einem zu "
            "kleinen Kader nur durch einen Kauf zu schliessen (§1.1, §4)"
        ),
    )


SCENARIOS: tuple[Scenario, ...] = (
    _debt_before_kickoff(),
    _healthy_and_quiet(),
    _injured_starter(),
    _open_lineup_slots(),
    _instant_sale_is_not_a_discount(),
    _bench_player_is_no_bargain(),
    _profit_peak(),
    _no_offers_means_no_accept(),
    _joker_is_no_starter(),
    _squad_is_full(),
    _underpay_is_blocked(),
    _bid_already_running(),
    _squad_too_small_to_field_eleven(),
)
