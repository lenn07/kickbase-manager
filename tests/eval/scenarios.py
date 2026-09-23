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

from app.application.decision_engine import DecisionContext
from app.application.player_enrichment import PlayerEnrichment
from app.domain.lineup import DEFAULT_FORMATION, LINEUP_SIZE, Lineup
from app.domain.models import (
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
) -> DecisionContext:
    """Baut eine Lage. `placed_in_lineup` steuert, wie viele Slots besetzt sind.

    Default ist eine vollständige Elf — sonst prüfte jedes Szenario nebenbei die
    -100-Regel statt der Regel, um die es eigentlich geht.
    """
    placed = LINEUP_SIZE if placed_in_lineup is None else placed_in_lineup
    squad = Squad(
        league_id=LEAGUE_ID,
        manager_id=MANAGER_ID,
        players=tuple(
            SquadPlayer(player=p, lineup_order=i if i < placed else None)
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
        open_bids_total=Decimal(0),
        now=NOW,
        next_matchday_start=NOW + timedelta(minutes=minutes_until_matchday),
        interval_min=120,
        enrichment=enrichment,
        max_negative_allowed=max_negative,
        current_balance_after_open_bids=Decimal(cash),
        lineup=Lineup(
            formation=DEFAULT_FORMATION,
            player_ids=tuple(p.id for p in squad_players[:placed]),
        ),
        lineup_deadline=NOW + timedelta(minutes=minutes_until_matchday),
    )


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
            {TradeAction.SELL, TradeAction.LIST_ON_MARKET, TradeAction.HOLD, TradeAction.BUY}
        ),
        forbidden=frozenset(),
        rule="These gebrochen (Verletzung) ist Verkaufsgrund 2 (§2.6)",
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


SCENARIOS: tuple[Scenario, ...] = (
    _debt_before_kickoff(),
    _healthy_and_quiet(),
    _injured_starter(),
    _open_lineup_slots(),
    _instant_sale_is_not_a_discount(),
    _bench_player_is_no_bargain(),
    _profit_peak(),
    _no_offers_means_no_accept(),
)
