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

from app.application.decision_engine import BuyRecord, DecisionContext, OpenBid
from app.application.player_enrichment import PlayerEnrichment
from app.domain.fixtures import TeamOutlook
from app.domain.lineup import DEFAULT_FORMATION, LINEUP_SIZE, Lineup
from app.domain.models import (
    LeagueConstraints,
    LeagueMe,
    LeagueRanking,
    ManagerStanding,
    MarketPlayer,
    Player,
    PlayerStatus,
    Position,
    Squad,
    SquadPlayer,
)
from app.domain.trade import TradeAction, TradeIntent

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
    # Obergrenze, gleiche Einheit. Der Gegenpart: ein Overbid ist ein Werkzeug,
    # kein Reflex. Ohne diese Prüfung ist jede Verschärfung der Overbid-Regeln
    # einseitig — ein Modell, das grundsätzlich +30 % bietet, wäre in allen
    # Szenarien grün und würde in der Praxis jede Trading-Marge verbrennen.
    max_bid_ratio: float | None = None
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
    team_id: str = "2",
) -> Player:
    return Player(
        id=pid,
        first_name="",
        last_name=name,
        team_id=team_id,
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
    mv_max_30d_pct: float = 1.05,
) -> PlayerEnrichment:
    """Ein unauffälliger Spieler: Startelf, durchspielend, Trend leicht positiv.

    `mv_max_30d_pct` ist der Abstand zum rollierenden 30-Tage-Hoch, als Faktor
    auf den Marktwert. Der Default lässt 5 % Luft — der Normalfall. Bis P2-13
    stand hier `mv_max_30d == market_value`, also **jeder** Spieler in **jedem**
    Szenario am 30-Tage-Hoch. Seit §3a „MW am `mv_max_30d` ist der Peak, nicht
    der Einstieg" eine Kaufbremse ist, hätte diese Voreinstellung jedes
    Kauf-Szenario nebenbei zu einem Peak-Szenario gemacht. Ein Szenario, das
    tatsächlich über den Peak geht, setzt den Wert ausdrücklich auf `1.0`.
    """
    return PlayerEnrichment(
        player_id=player.id,
        market_trend_1d_pct=trend_1d,
        market_trend_3d_pct=trend_7d,
        market_trend_7d_pct=trend_7d,
        market_trend_30d_pct=trend_7d,
        mv_max_30d=int(player.market_value * Decimal(str(mv_max_30d_pct))),
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


def _neutral_outlook(team_id: str) -> TeamOutlook:
    """Ein Gegner aus dem Tabellenmittelfeld, Heimspiel — bewusst unauffällig.

    Seit P2-11 stehen Gegner und Schwierigkeit im Payload. Ohne einen Default
    hier trüge **jeder** Spieler in **jedem** Szenario `missing_data:fixtures`,
    und §1.2 verlangt dann ausdrücklich, ohne Spielplan zu entscheiden — die
    Eval würde also weiter den Zustand vor P2-11 messen. Umgekehrt darf der
    Default nicht auffällig sein: ein Szenario, in dem alle gegen Bayern
    spielen, prüft nebenbei die Gegnerstärke statt der gemeinten Regel. Genau
    dieselbe Überlegung wie bei `mv_max_30d_pct` (siehe `_enrichment`).
    """
    return TeamOutlook(
        team_id=team_id,
        next_opponent_id="99",
        next_opponent_name="Mittelfeld-Gegner",
        next_opponent_rank=9,
        is_home=True,
        fdr=3,
        fdr_next3=3.0,
        next_kickoff=NOW + timedelta(days=3),
        next_matchday=5,
    )


def _ranking(
    *,
    my_rank: int = 2,
    spread: int = 200,
    matchday: int = 4,
    total_matchdays: int = 34,
    managers_total: int = 4,
) -> LeagueRanking:
    """Eine Ligatabelle, in der ich auf `my_rank` stehe.

    `spread` ist der Punktabstand zwischen zwei benachbarten Rängen — daraus
    folgt alles andere: der Rückstand auf Platz 1 ist `(my_rank - 1) * spread`,
    der Vorsprung auf den Verfolger ebenfalls `spread`. Eine Zahl steuert die
    ganze Lage, in beide Richtungen.

    Default: Platz 2 von 4, 200 Punkte hinter dem Führenden, Spieltag 4 von 34
    — unauffällig und früh in der Saison. Dieselbe Überlegung wie beim
    Spielplan-Default: ohne Ligakontext trüge jedes Szenario
    `missing_data:league_ranking` und der Prompt entschiede ausdrücklich ohne
    ihn; mit einem auffälligen (großer Rückstand, Saisonende) prüfte jedes
    Szenario nebenbei den Risikoappetit statt der gemeinten Regel.
    """
    last_place_points = 3000
    return LeagueRanking(
        league_id=LEAGUE_ID,
        league_name="Eval-Liga",
        matchday=matchday,
        total_matchdays=total_matchdays,
        managers=tuple(
            ManagerStanding(
                manager_id=MANAGER_ID if rank == my_rank else f"rival-{rank}",
                name=f"Manager {rank}",
                season_points=last_place_points + (managers_total - rank) * spread,
                matchday_points=0,
                rank=rank,
                team_value=Decimal(150_000_000),
            )
            for rank in range(1, managers_total + 1)
        ),
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
    held_days: dict[str, int] | None = None,
    # 4 h, nicht 8: beim Default-Listing (6 h Restlaufzeit) liegt damit **ein**
    # Marktwert-Update vor dem Zuschlag — der Normalfall. Bei 8 h lief jedes
    # Default-Listing vor dem Update ab, `mv_updates_until_expiry` stand überall
    # auf 0, und seit §3a daraus eine harte Kaufbremse ist („ohne weiteres Update
    # kann der Trade keinen Gewinn machen"), war der Default identisch mit der
    # ausdrücklich gemeinten Ausnahme aus `no_trade_without_a_mv_update`. Im
    # bezahlten Lauf vom 2026-09-26 endeten sechs Szenarien mit genau dieser
    # Begründung auf HOLD, und `underpay_is_blocked` meldete grün für eine
    # Gebots-Regel, die es nie geprüft hat. Der Wächter dazu steht in
    # `test_scenarios_build.py`.
    hours_until_mv_update: int = 4,
    team_outlook_overrides: dict[str, TeamOutlook] | None = None,
    league_ranking: LeagueRanking | None = None,
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
    outlook = {p.team_id: _neutral_outlook(p.team_id) for p in squad_players + market_players}
    outlook.update(team_outlook_overrides or {})

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
        # Das nächste 22-Uhr-Update. Ohne diesen Wert stehen
        # `mv_updates_until_matchday` und `mv_updates_until_expiry` auf `null` —
        # und damit fehlt der Eval genau die Uhr, an der §3 den Drift-Anteil des
        # Overbids und §3a die Frage „bleibt überhaupt Zeit für einen Trade?"
        # aufhängt.
        mv_update_at=NOW + timedelta(hours=hours_until_mv_update),
        interval_min=120,
        # Trade-Positionen mit Haltedauer. `days_held` im Payload entscheidet,
        # ob eine Position frisch ist oder seit Tagen einen Kaderplatz belegt,
        # ohne Rendite zu bringen (§3a, Verkaufssignal 5).
        buy_history={
            pid: BuyRecord(
                intent=TradeIntent.PROFIT,
                buy_price=Decimal(entry_prices.get(pid, 0)) or _market_value_of(squad_players, pid),
                bought_at=NOW - timedelta(days=days),
            )
            for pid, days in (held_days or {}).items()
        },
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
        team_outlook=outlook,
        league_ranking=league_ranking or _ranking(),
    )


def _market_value_of(players: list[Player], player_id: str) -> Decimal:
    for p in players:
        if p.id == player_id:
            return p.market_value
    return Decimal(0)


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

    Geprüft wird, dass der Bot **nicht** in den Sofortverkauf geht, obwohl er
    kein Kapital braucht: `SELL_INSTANT` verschenkt dann den Listing-Aufschlag,
    ohne etwas zu lösen.

    `HOLD` und `BUY` sind beide zulässig, und das ist seit P2-13 kein
    Widerspruch mehr, sondern der Punkt: die Lage steht in der `trading`-Phase
    mit vier freien Kaderplätzen, aber der einzige Marktspieler hat 60 Punkte
    Schnitt und einen flachen Trend. Ein Kauf ist damit vertretbar, ein `HOLD`
    mit Befund ebenfalls. Was §3a verlangt, ist kein Handeln um jeden Preis,
    sondern eine Begründung — und die misst dieses Szenario nicht, sondern
    `trading_window_fills_free_slots`, wo die Kandidaten eindeutig sind.
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
        # Der Marktspieler ist ausdrücklich **kein** Trade-Kandidat: fallender
        # Trend, Listing endet vor dem nächsten 22-Uhr-Update. Seit §3a dem
        # Trading in dieser Phase Vorrang gibt, würde ein unauffällig-positiver
        # Default hier eine zweite, konkurrierende Handlungsoption aufmachen —
        # und das Szenario prüfte dann nicht mehr den Peak-Exit, sondern welche
        # von zwei vertretbaren Aktionen das Modell vorzieht.
        market[0].id: _enrichment(market[0], trend_7d=-1.0, trend_1d=-0.4),
        peaked.id: _enrichment(
            peaked,
            trend_7d=18.0,
            trend_1d=-1.2,
            start_probability=0.9,
            # Der Marktwert **ist** hier das 30-Tage-Hoch — das ist die halbe
            # Aussage des Szenarios und stand seit P2-13 nicht mehr im Default.
            mv_max_30d_pct=1.0,
        ),
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
    `my_open_bid_count` steht auf 1 — und das ist seit P2-13 die korrekte
    Lesart: der Zähler meint auf fremden Listings die **eigenen** Gebote, nicht
    die der anderen. Vorher las der Prompt ihn als Konkurrenzmaß und leitete
    aus `offer_count == 1` ab, man sei der einzige Bieter; richtig ist, dass
    über fremde Gebote gar keine Aussage vorliegt. Die Regel bleibt dieselbe:
    ein gleich hohes Nachgebot ändert nichts und verbrennt den Tick.

    `BUY` bleibt als Aktion erlaubt: es gibt einen **zweiten** Marktspieler
    ohne laufendes Gebot, und ihn zu kaufen ist völlig richtig. Verboten ist
    allein die Wiederholung auf den ersten.
    """
    squad = _squad_of_twelve()
    running = _player("940", "Läuft schon", Position.MIDFIELDER, 11_000_000, average_points=150.0)
    free = _player("941", "Noch frei", Position.DEFENDER, 8_000_000, average_points=130.0)
    return Scenario(
        name="bid_already_running",
        description="Eigenes Gebot über 11 Mio läuft, my_open_bid_count=1, Kader hat Platz",
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
            "`my_open_bid_price != null` ist ein gleich hohes Nachgebot wirkungslos, und "
            "`my_open_bid_count` zählt die eigenen Gebote, nicht die fremden (§3)"
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


def _overbid_covers_the_mv_drift() -> Scenario:
    """Der Marktwert steigt bis zum Zuschlag — ein Gebot zum heutigen Wert platzt.

    Die härteste Preisregel des Spiels, und die einzige, die sich ohne Annahmen
    über fremde Bieter herleiten lässt: maßgeblich ist der Marktwert **zum
    Transferzeitpunkt**. Der Spieler hier steigt 2,5 % pro Tag, das Listing läuft
    über zwei 22-Uhr-Updates (`mv_updates_until_expiry: 2`) — beim Zuschlag steht
    er rund 5 % höher. Wer den heutigen Marktwert bietet, bekommt ihn nicht und
    hat den Tick verloren.

    Das Szenario misst deshalb die **Höhe**, nicht die Aktion: `HOLD` bleibt
    erlaubt (es gibt gute Gründe, einen steigenden Spieler nicht zu jagen), aber
    ein Gebot muss den Drift-Anteil tragen. `min_bid_ratio` liegt bei 1,02 und
    damit unter den gerechneten 5 % — geprüft wird, dass das Modell die Rechnung
    überhaupt macht, nicht dass es sie auf die Kommastelle trifft.
    """
    squad = _squad_of_twelve()
    riser = _player("960", "Steigt schnell", Position.MIDFIELDER, 9_000_000, average_points=135.0)
    overrides = {
        riser.id: _enrichment(riser, trend_7d=9.0, trend_1d=2.5, mv_max_30d_pct=1.12),
    }
    return Scenario(
        name="overbid_covers_the_mv_drift",
        description="Marktspieler +2,5 %/Tag, Listing läuft über zwei MW-Updates",
        context=_context(
            squad_players=squad,
            market_players=[riser],
            cash=30_000_000,
            team_value=150_000_000,
            minutes_until_matchday=6 * 24 * 60,
            enrichment_overrides=overrides,
            # 40 h Restlaufzeit, nächstes Update in 8 h → zwei Updates bis zum
            # Zuschlag.
            market_expiry_s=40 * 3600,
        ),
        allowed=frozenset({TradeAction.BUY, TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        min_bid_ratio=1.02,
        rule=(
            "Massgeblich ist der Marktwert zum Transferzeitpunkt: `market_trend_1d_pct` x "
            "`mv_updates_until_expiry` gehoert als Drift-Anteil ins Gebot, sonst wird es "
            "beim Zuschlag abgelehnt (§3)"
        ),
    )


def _no_trade_without_a_mv_update() -> Scenario:
    """Ohne ein weiteres 22-Uhr-Update kann ein Trade keinen Gewinn machen.

    Das Nicht-Kauf-Signal aus §3a, und der Gegenpol zu
    `overbid_covers_the_mv_drift`: derselbe flache Kandidat, aber das Listing
    läuft **vor** dem nächsten Marktwert-Update ab
    (`mv_updates_until_expiry: 0`). Marktwerte bewegen sich ausschließlich zu
    diesen Zeitpunkten — ein Trade-Kauf bindet hier Geld und einen Kaderplatz
    für eine Wertentwicklung, die es in seiner Laufzeit nicht gibt. Die Lage ist
    bewusst einladend (Trading-Phase, vier freie Plätze, 25 Mio Cash), damit
    geprüft wird, ob die Zeitrechnung gegen den Handlungsdruck aus §1 besteht.

    **Was dieses Szenario NICHT prüft:** die Overbid-Obergrenze. Sie stand hier
    bis zum ersten bezahlten Lauf (2026-09-26) und war wirkungslos — das Modell
    wählte dreimal `HOLD`, und eine Gebots-Schranke greift nur bei `BUY`. Sie
    sitzt jetzt in `trading_window_fills_free_slots`, wo `BUY` die einzige
    erlaubte Aktion ist. `max_bid_ratio` bleibt hier als Sicherung stehen, falls
    doch gekauft wird, ist aber nicht der Zweck.
    """
    squad = _squad_of_twelve()
    flat = _player("961", "Unauffaellig", Position.DEFENDER, 7_000_000, average_points=95.0)
    overrides = {flat.id: _enrichment(flat, trend_7d=0.3, trend_1d=0.1)}
    return Scenario(
        name="no_trade_without_a_mv_update",
        description="Flacher Trend, Listing endet vor dem MW-Update, Kickbase-Listing",
        context=_context(
            squad_players=squad,
            market_players=[flat],
            cash=25_000_000,
            team_value=150_000_000,
            minutes_until_matchday=5 * 24 * 60,
            enrichment_overrides=overrides,
            # Läuft vor dem nächsten 22-Uhr-Update ab: kein Drift-Anteil. Beide
            # Zahlen gehören zusammen und stehen deshalb beide hier — der
            # Default sorgt inzwischen für das Gegenteil (ein Update vor dem
            # Zuschlag), und diese Ausnahme darf nicht von ihm abhängen.
            market_expiry_s=5 * 3600,
            hours_until_mv_update=8,
        ),
        allowed=frozenset({TradeAction.BUY, TradeAction.HOLD, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset({TradeAction.ACCEPT_OFFER, TradeAction.DECLINE_OFFER}),
        min_bid_ratio=1.0,
        max_bid_ratio=1.06,
        rule=(
            "`mv_updates_until_expiry == 0` heisst: bis zum Zuschlag bewegt sich kein "
            "Marktwert. Ein Trade-Kauf rechnet sich dann nicht, und der Aufschlag hat "
            "ohnehin keine Grundlage (§3a, §3)"
        ),
    )


def _trading_window_fills_free_slots() -> Scenario:
    """Sechs Tage bis zum Anpfiff, vier freie Kaderplätze, drei klare Steiger.

    Das Szenario zur Zielhierarchie aus §1: in der `trading`-Phase steht das
    Marktwert-Trading **vor** der Punkte-Optimierung, weil Punkte einmal pro
    Spieltag anfallen und Marktwert-Gewinne jeden Tag um 22:00 Uhr. Vier leere
    Plätze bei 16 Kaderslots sind vier Positionen, die nichts verdienen, und
    `mv_updates_until_matchday` steht auf 6 — es ist reichlich Zeit, den Zuwachs
    einzusammeln und vor dem Anpfiff wieder zu realisieren.

    Die Elf steht vollständig, das Konto ist im Plus, alle drei Kandidaten sind
    günstig, fit, gesetzt, steigen seit Tagen und haben Abstand zum 30-Tage-Hoch.
    Es gibt damit keinen Befund, der `HOLD` trägt — und genau das ist die Regel,
    die hier geprüft wird. Ohne dieses Szenario bleibt jede Trading-Regel im
    Prompt unbelegt: `HOLD` ist in allen anderen Lagen erlaubt, die Eval würde
    also weiter nur messen, dass der Bot nichts falsch macht.
    """
    squad = _squad_of_twelve()
    risers = [
        _player("970", "Steiger Abwehr", Position.DEFENDER, 4_500_000, average_points=105.0),
        _player("971", "Steiger Mittelfeld", Position.MIDFIELDER, 5_500_000, average_points=115.0),
        _player("972", "Steiger Sturm", Position.FORWARD, 3_800_000, average_points=98.0),
    ]
    overrides = {
        p.id: _enrichment(p, trend_7d=6.5, trend_1d=1.4, mv_max_30d_pct=1.15) for p in risers
    }
    return Scenario(
        name="trading_window_fills_free_slots",
        description="Anpfiff in 6 Tagen, 12/16 Kaderplätze belegt, 3 günstige Steiger, 22 Mio Cash",
        context=_context(
            squad_players=squad,
            market_players=risers,
            cash=22_000_000,
            team_value=150_000_000,
            minutes_until_matchday=6 * 24 * 60,
            enrichment_overrides=overrides,
            market_expiry_s=30 * 3600,
        ),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset(
            {
                TradeAction.HOLD,
                TradeAction.SELL,
                TradeAction.ACCEPT_OFFER,
                TradeAction.DECLINE_OFFER,
            }
        ),
        min_bid_ratio=1.0,
        # **Hier** sitzt die Overbid-Obergrenze, und zwar aus einem Grund: `BUY`
        # ist die einzige erlaubte Aktion, es fällt also garantiert ein Gebot,
        # das geprüft werden kann. Im ersten bezahlten Lauf (2026-09-26) stand
        # sie in `no_trade_without_a_mv_update` — dort wählte das Modell dreimal
        # `HOLD`, und eine Gebots-Schranke greift nur bei `BUY`. Die Assertion
        # war also nie ausgeführt und das Szenario meldete grün für eine Regel,
        # die es nicht gemessen hat.
        #
        # 8 % ist aus der Lage hergeleitet, nicht geraten: Drift bis zum Zuschlag
        # ist ~1,4 % (ein Update), der erwartete Zuwachs bis zum Anpfiff ~8 %
        # (sechs Updates). Nach der Deckelung aus §3 („Zuwachs minus Aufschlag >=
        # Zielmarge") ist ein Aufschlag jenseits davon ein Trade, der bei null
        # startet.
        max_bid_ratio=1.08,
        rule=(
            "In der `trading`-Phase gehen Marktwert-Gewinne vor Punkten, und freie "
            "Kaderplaetze sind Positionen ohne Rendite. `HOLD` braucht hier einen Befund "
            "an den Zahlen (§1, §3a). Der Aufschlag bleibt durch die erwartete Rendite "
            "gedeckt (§3, Deckelung bei `intent: PROFIT`)"
        ),
    )


def _stale_trade_frees_the_slot() -> Scenario:
    """Kader voll, eine Trade-Position liegt seit neun Tagen ohne Bewegung.

    Die Slot-Ökonomie aus §3a: beim Trading ist der knappe Rohstoff nicht das
    Geld, sondern der Kaderplatz. Die Position hier (`days_held: 9`,
    `bought_intent: PROFIT`) sitzt auf der Bank, ihr Trend ist negativ, und sie
    hat in neun Tagen nichts gebracht — während am Markt ein Spieler mit
    1,6 %/Tag liegt und `mv_updates_until_matchday` auf 5 steht.

    `BUY` ist bei `squad_slots_left: 0` regelseitig ausgeschlossen (Kickbase
    lehnt das Gebot schon bei der Abgabe ab), die Reihenfolge ist also: dieser
    Tick verkauft, der nächste kauft. `HOLD` ist verboten, weil es die einzige
    Aktion ist, die den Platz nicht freimacht — und der Platz ist hier der
    ganze Punkt. Der Verkauf trifft bewusst keinen Startelf-Spieler: das
    Szenario soll über Kapitalumschlag gehen, nicht über Punkte.
    """
    squad = _squad_of_twelve()
    # Auf 16 auffüllen — der Kader ist voll, `squad_slots_left` also 0.
    squad += [
        _player("504", "Reserve Abwehr", Position.DEFENDER, 4_000_000, average_points=45.0),
        _player("505", "Reserve Mittelfeld", Position.MIDFIELDER, 4_200_000, average_points=50.0),
        _player("506", "Reserve Sturm", Position.FORWARD, 4_400_000, average_points=48.0),
        _player("507", "Totes Kapital", Position.MIDFIELDER, 6_000_000, average_points=55.0),
    ]
    stale = squad[-1]
    fresh = _player("973", "Laeuft heiss", Position.MIDFIELDER, 5_000_000, average_points=120.0)
    overrides = {
        stale.id: _enrichment(stale, trend_7d=-0.2, trend_1d=-0.3, start_probability=0.15),
        fresh.id: _enrichment(fresh, trend_7d=7.0, trend_1d=1.6, mv_max_30d_pct=1.18),
    }
    return Scenario(
        name="stale_trade_frees_the_slot",
        description="16/16 Kaderplätze, Trade seit 9 Tagen bei -0,2 %, starker Kandidat am Markt",
        context=_context(
            squad_players=squad,
            market_players=[fresh],
            cash=12_000_000,
            team_value=150_000_000,
            minutes_until_matchday=5 * 24 * 60,
            enrichment_overrides=overrides,
            # Eingekauft zum damaligen Marktwert, seither seitwärts.
            buy_prices={stale.id: 6_050_000},
            held_days={stale.id: 9},
            squad_limit=16,
            market_expiry_s=20 * 3600,
        ),
        allowed=frozenset({TradeAction.SELL, TradeAction.LIST_ON_MARKET}),
        forbidden=frozenset(
            {
                TradeAction.BUY,
                TradeAction.HOLD,
                TradeAction.ACCEPT_OFFER,
                TradeAction.DECLINE_OFFER,
            }
        ),
        rule=(
            "Eine Trade-Position ohne Bewegung kostet den Kaderplatz, nicht den Preis — "
            "verkaufen ist richtig, auch ohne Gewinn. Bei `squad_slots_left: 0` kommt der "
            "Verkauf zuerst, der Kauf im naechsten Tick (§3a)"
        ),
    )


def _easier_fixture_wins_the_duel() -> Scenario:
    """Zehn Spieler, ein leerer Startelf-Slot, zwei Stürmer zur Auswahl.

    Der Fall, für den P2-11 gebaut wurde. Bis dahin standen Restspielplan und
    Gegnerstärke nicht im Payload, und §1.2 verbot ausdrücklich, mit ihnen zu
    rechnen — die beiden Kandidaten hier waren für das Modell **nicht
    unterscheidbar**: gleiche Position, gleicher Marktwert, gleiche Punkte,
    gleiche Form, gleicher Trend. Der einzige Unterschied ist das nächste Spiel:
    einer zu Hause gegen den Tabellenletzten (`fdr` 1), einer auswärts beim
    Tabellenführer (`fdr` 5).

    **Warum der Kader zu klein ist.** Die erste Fassung dieses Szenarios stand
    mit vollständiger Elf in der `matchday_prep`-Phase — und das Modell wählte im
    bezahlten Lauf vom 2026-09-26 dreimal `HOLD`. Formal grün (HOLD ist erlaubt,
    der verbotene Spieler wurde nicht gewählt), inhaltlich wertlos: eine Regel
    über die *Auswahl* wird nur geprüft, wenn überhaupt gewählt wird. Genau
    derselbe Befund wie bei `max_bid_ratio` in P2-13. Mit zehn Spielern und einem
    leeren Startelf-Slot ist Nichtstun garantiert -100 Punkte wert und mit
    `SET_LINEUP` nicht heilbar — es gibt keinen elften Spieler. `BUY` fällt also,
    und die Auswahl ist messbar.

    Es ist gleichzeitig der Wächter gegen eine verdrehte FDR-Skala: bei
    invertierter Richtung wählt das Modell zuverlässig den falschen Spieler.
    """
    squad = [
        _player("101", "Keeper", Position.GOALKEEPER, 8_000_000),
        *[_player(f"20{i}", f"Abwehr{i}", Position.DEFENDER, 12_000_000) for i in range(1, 5)],
        *[
            _player(f"30{i}", f"Mittelfeld{i}", Position.MIDFIELDER, 15_000_000)
            for i in range(1, 5)
        ],
        _player("401", "Sturm1", Position.FORWARD, 18_000_000),
    ]
    easy = _player(
        "980",
        "Heim gegen Letzten",
        Position.FORWARD,
        9_000_000,
        average_points=120.0,
        team_id="28",
    )
    hard = _player(
        "981",
        "Auswaerts beim Ersten",
        Position.FORWARD,
        9_000_000,
        average_points=120.0,
        team_id="15",
    )
    # Identisch bis auf die team_id — jede Abweichung hier würde das Szenario auf
    # ein anderes Kriterium umlenken.
    overrides = {p.id: _enrichment(p) for p in (easy, hard)}
    outlook = {
        "28": TeamOutlook(
            team_id="28",
            next_opponent_id="15",
            next_opponent_name="Tabellenletzter",
            next_opponent_rank=18,
            is_home=True,
            fdr=1,
            fdr_next3=1.3,
            next_kickoff=NOW + timedelta(days=2),
            next_matchday=5,
        ),
        "15": TeamOutlook(
            team_id="15",
            next_opponent_id="28",
            next_opponent_name="Tabellenfuehrer",
            next_opponent_rank=1,
            is_home=False,
            fdr=5,
            fdr_next3=4.7,
            next_kickoff=NOW + timedelta(days=2),
            next_matchday=5,
        ),
    }
    return Scenario(
        name="easier_fixture_wins_the_duel",
        description=(
            "10 Spieler, 1 leerer Startelf-Slot, zwei identische Stuermer — "
            "einer heim gegen Platz 18, einer auswaerts bei Platz 1"
        ),
        context=_context(
            squad_players=squad,
            market_players=[easy, hard],
            cash=20_000_000,
            team_value=120_000_000,
            minutes_until_matchday=2 * 24 * 60,
            placed_in_lineup=len(squad),
            enrichment_overrides=overrides,
            team_outlook_overrides=outlook,
            # Beide Listings laufen lange vor dem Anpfiff ab: das Warten hat
            # keinen Grund, und ein MW-Update liegt vor dem Zuschlag.
            market_expiry_s=12 * 3600,
        ),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL, TradeAction.ACCEPT_OFFER}),
        forbidden_player_ids=frozenset({hard.id}),
        expects_full_lineup=False,
        min_bid_ratio=1.0,
        rule=(
            "`fdr` 1 = leichtester Gegner, 5 = schwerster. Bei sonst gleichen Spielern "
            "entscheidet der Spielplan; die Defensive/Offensive gegen schwache Gegner ist "
            "die verlaesslichste Punktequelle (§1.2)"
        ),
    )


def _late_season_squad_with_one_gap() -> list[Player]:
    """Zehn Spieler — ein Startelf-Slot bleibt leer und ist nur durch Kauf zu füllen.

    Dieselbe Konstruktion wie in `easier_fixture_wins_the_duel`, und aus
    demselben Grund: eine Regel über die **Auswahl** wird nur geprüft, wenn das
    Modell überhaupt wählt. Mit vollständiger Elf ist `HOLD` vertretbar, und das
    Szenario meldet grün, ohne die Regel berührt zu haben.
    """
    return [
        _player("101", "Keeper", Position.GOALKEEPER, 8_000_000),
        *[_player(f"20{i}", f"Abwehr{i}", Position.DEFENDER, 12_000_000) for i in range(1, 5)],
        *[
            _player(f"30{i}", f"Mittelfeld{i}", Position.MIDFIELDER, 15_000_000)
            for i in range(1, 5)
        ],
        _player("401", "Sturm1", Position.FORWARD, 18_000_000),
    ]


def _variance_pair() -> tuple[Player, Player]:
    """Zwei Stürmer mit demselben Preis: der eine sicher, der andere volatil.

    „Sicher" heißt hier: gesetzt (95 % Startelf), durchspielend, aber mit
    gedeckeltem Ertrag (95 Punkte Schnitt). „Volatil" heißt: doppelt so hoher
    Schnitt bei halber Einsatzsicherheit — ein Spieler, der ein Spiel entscheidet
    oder gar nicht aufläuft. Der **Erwartungswert** beider liegt nah beieinander
    (95 gegen 0,5 x 190); was sie unterscheidet, ist die Streuung.
    """
    safe = _player("990", "Sicherer Ertrag", Position.FORWARD, 9_000_000, average_points=95.0)
    volatile = _player("991", "Hohes Ceiling", Position.FORWARD, 9_000_000, average_points=190.0)
    return safe, volatile


def _variance_enrichment(safe: Player, volatile: Player) -> dict[str, PlayerEnrichment]:
    return {
        safe.id: _enrichment(safe, start_probability=0.95, minutes_last5=89.0, starts_last5=5),
        volatile.id: _enrichment(
            volatile, start_probability=0.50, minutes_last5=55.0, starts_last5=2
        ),
    }


def _trailing_late_needs_variance() -> Scenario:
    """Platz 4 von 4, 3000 Punkte zurück, zwei Spieltage übrig.

    Der Fall, für den P2-12 gebaut wurde. Bis dahin stand im Payload kein Feld
    dazu, wo der Manager in der Liga steht — jede Lage sah aus wie die erste
    Woche einer offenen Saison, und das Modell maximierte immer denselben
    Erwartungswert.

    Bei Saisonpunkten gewinnt die Summe. Wer 3000 Punkte zurückliegt und noch
    zwei Spieltage hat, holt das mit dem sicheren 95-Punkte-Mann **nicht** auf —
    der erhöht den Erwartungswert und lässt den Abstand, wo er ist. Die einzige
    Linie, die noch zu einem anderen Ergebnis führt, ist Varianz. Umgekehrt
    gespiegelt in `leading_late_protects_the_lead`: identische Lage, nur
    führend statt letzter, und dort ist derselbe Spieler die falsche Wahl.

    Verboten ist deshalb die **Auswahl** des sicheren Spielers, nicht der Kauf.
    """
    safe, volatile = _variance_pair()
    squad = _late_season_squad_with_one_gap()
    return Scenario(
        name="trailing_late_needs_variance",
        description="Platz 4/4, 3000 Punkte zurueck, 2 Spieltage uebrig, 1 leerer Startelf-Slot",
        context=_context(
            squad_players=squad,
            market_players=[safe, volatile],
            cash=20_000_000,
            team_value=120_000_000,
            minutes_until_matchday=2 * 24 * 60,
            placed_in_lineup=len(squad),
            enrichment_overrides=_variance_enrichment(safe, volatile),
            market_expiry_s=12 * 3600,
            league_ranking=_ranking(my_rank=4, spread=1000, matchday=32),
        ),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL, TradeAction.ACCEPT_OFFER}),
        forbidden_player_ids=frozenset({safe.id}),
        expects_full_lineup=False,
        min_bid_ratio=1.0,
        rule=(
            "Bei Saisonpunkten zaehlt die Summe: 3000 Punkte Rueckstand bei 2 Restspieltagen "
            "sind mit dem sicheren Erwartungswert nicht aufzuholen, nur mit Varianz "
            "(§1, Wertungsmodus + `league.points_behind_leader` x `league.matchdays_left`)"
        ),
    )


def _leading_late_protects_the_lead() -> Scenario:
    """Dieselbe Lage, nur führend: Platz 1 mit 3000 Punkten Vorsprung.

    Das Gegenstück zu `trailing_late_needs_variance` — gleicher Kader, gleicher
    Markt, gleiche zwei Restspieltage, gespiegelter Ligakontext. Wer 3000 Punkte
    vorn liegt, braucht keinen Spieler, der ein Spiel entscheidet **oder gar
    nicht aufläuft**; er braucht die 95 sicheren Punkte. Varianz kann hier nur
    schaden, weil sie die einzige Möglichkeit ist, den Vorsprung noch zu
    verlieren.

    Erst beide Szenarien zusammen belegen die Regel: ein Modell, das den
    Ligakontext ignoriert, wählt in beiden denselben Spieler und fällt genau in
    einem von beiden durch.
    """
    safe, volatile = _variance_pair()
    squad = _late_season_squad_with_one_gap()
    return Scenario(
        name="leading_late_protects_the_lead",
        description="Platz 1/4, 3000 Punkte Vorsprung, 2 Spieltage uebrig, 1 leerer Startelf-Slot",
        context=_context(
            squad_players=squad,
            market_players=[safe, volatile],
            cash=20_000_000,
            team_value=120_000_000,
            minutes_until_matchday=2 * 24 * 60,
            placed_in_lineup=len(squad),
            enrichment_overrides=_variance_enrichment(safe, volatile),
            market_expiry_s=12 * 3600,
            league_ranking=_ranking(my_rank=1, spread=3000, matchday=32),
        ),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL, TradeAction.ACCEPT_OFFER}),
        forbidden_player_ids=frozenset({volatile.id}),
        expects_full_lineup=False,
        min_bid_ratio=1.0,
        rule=(
            "Ein Vorsprung von 3000 Punkten bei 2 Restspieltagen wird mit dem sicheren "
            "Ertrag verteidigt, nicht mit Varianz — sie ist der einzige Weg, ihn noch zu "
            "verlieren (§1, Wertungsmodus + `league`-Block)"
        ),
    )


def _pure_trading_candidate() -> Player:
    """Ein reiner Marktwert-Kandidat: steigt, punktet aber kaum.

    Die Trennung ist der ganze Punkt von P2-14. Ein Spieler, der *beides* kann,
    wäre auch am 33. Spieltag ein richtiger Kauf — dann prüfte das Szenario
    nicht die Saisonphase, sondern nur, ob das Modell gute Spieler erkennt.
    40 Punkte Schnitt bei 50 % Startelf schließen den Punkte-Grund aus; bleibt
    der Marktwert, und genau der verliert zum Saisonende seinen Zweck.
    """
    return _player(
        "995", "Steigt, punktet nicht", Position.DEFENDER, 5_000_000, average_points=40.0
    )


def _season_phase_context(*, matchday: int) -> DecisionContext:
    """Dieselbe Lage, zweimal — einziger Unterschied ist der Spieltag.

    Vier freie Kaderplätze, Elf vollständig, Konto im Plus, sechs Tage bis zum
    Anpfiff und ein klar steigender Kandidat. In der `trading`-Phase ist das
    die Lage, in der §3a einen Kauf verlangt (`trading_window_fills_free_slots`
    misst genau das). Ob er noch legitim ist, hängt allein an
    `league.matchdays_left`.
    """
    riser = _pure_trading_candidate()
    return _context(
        squad_players=_squad_of_twelve(),
        market_players=[riser],
        cash=22_000_000,
        team_value=150_000_000,
        minutes_until_matchday=6 * 24 * 60,
        enrichment_overrides={
            riser.id: _enrichment(
                riser,
                trend_7d=6.5,
                trend_1d=1.4,
                start_probability=0.50,
                minutes_last5=45.0,
                starts_last5=1,
                mv_max_30d_pct=1.15,
            )
        },
        market_expiry_s=30 * 3600,
        league_ranking=_ranking(matchday=matchday),
    )


def _early_season_trades_for_capital() -> Scenario:
    """Spieltag 4 von 34: 30 Spieltage, um Kapital in Punkte zu verwandeln.

    Die Gegenprobe zu `endgame_stops_pure_trading`. Ohne sie hiesse ein grünes
    Endgame-Szenario nur „der Bot kauft nichts" — erst der Kontrast belegt,
    dass er nach der **Saisonphase** unterscheidet und nicht nach dem Spieler.
    """
    return Scenario(
        name="early_season_trades_for_capital",
        description="Spieltag 4/34, 4 freie Kaderplaetze, klarer Steiger, 22 Mio Cash",
        context=_season_phase_context(matchday=4),
        allowed=frozenset({TradeAction.BUY}),
        forbidden=frozenset({TradeAction.HOLD, TradeAction.SELL, TradeAction.ACCEPT_OFFER}),
        min_bid_ratio=1.0,
        max_bid_ratio=1.08,
        rule=(
            "In der `trading`-Phase bei `season_phase: regular` gehen Marktwert-Gewinne vor "
            "Punkten; ein freier Kaderplatz ist eine Position ohne Rendite (§1, §3a)"
        ),
    )


def _endgame_stops_pure_trading() -> Scenario:
    """Spieltag 31 von 34: derselbe Steiger, drei Spieltage übrig.

    Marktwert ist kein Siegkriterium, sondern Kapital — und Kapital zählt erst,
    wenn es in Spieler umgesetzt ist, die noch auflaufen. Die Kette aus Kaufen,
    Halten, Verkaufen und Nachkaufen reicht in drei Spieltagen nicht mehr
    durch; am letzten Spieltag ist ein volles Konto exakt null Punkte wert.

    Der Kandidat trägt hier die Beweislast: 40 Punkte Schnitt bei 50 %
    Startelf-Wahrscheinlichkeit taugt nicht als Punkte-Kauf, also bleibt nur
    der Marktwert-Grund — und der ist weg. `BUY` ist damit die einzige Aktion,
    die diese Lage verbietet.
    """
    return Scenario(
        name="endgame_stops_pure_trading",
        description="Spieltag 31/34, 3 Spieltage uebrig, sonst identisch zum Trading-Szenario",
        context=_season_phase_context(matchday=31),
        allowed=frozenset({TradeAction.HOLD, TradeAction.LIST_ON_MARKET, TradeAction.SELL}),
        forbidden=frozenset({TradeAction.BUY, TradeAction.ACCEPT_OFFER}),
        rule=(
            "`season_phase: endgame` nimmt das Trading aus der Zielhierarchie: ein Kauf allein "
            "wegen steigendem Marktwert kann bis zum Saisonende nicht mehr in Punkte "
            "umgesetzt werden (§1)"
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
    # P2-13: Overbid-Kalibrierung ohne Konkurrenz-Zaehler + Trading-Playbook.
    _overbid_covers_the_mv_drift(),
    _no_trade_without_a_mv_update(),
    _trading_window_fills_free_slots(),
    _stale_trade_frees_the_slot(),
    # P2-11: Spielplan & Gegnerstaerke.
    _easier_fixture_wins_the_duel(),
    # P2-12: Ligakontext — dieselbe Lage, gespiegelter Tabellenstand.
    _trailing_late_needs_variance(),
    _leading_late_protects_the_lead(),
    # P2-14: Saisonphasen-Gewichtung — dieselbe Lage, frueh und spaet in der Saison.
    _early_season_trades_for_capital(),
    _endgame_stops_pure_trading(),
)
