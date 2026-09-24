"""P1-6 — der Einstand kommt aus Kickbase, nicht aus dem eigenen Trade-Log.

Der Optimizing-Plan §3.3 führte `prc` als verifiziertes Feld des
Squad-Response. Die echte Cassette widerspricht: `prc` steht dort **nicht**,
`mvgl` schon. Weil `mvgl = mv - prc` gilt, ist der Einstand trotzdem exakt
rekonstruierbar — und diese Datei belegt das gegen eine unabhängige Quelle,
statt es zu behaupten.

Unabhängige Quelle ist `/managers/{m}/transfer`: dort steht mit `trp` der
tatsächlich bezahlte Preis. Deckt sich `mv - mvgl` damit, ist die Ableitung
richtig; weicht sie ab, ist die Annahme über `mvgl` falsch und das Paket
verkauft eine erfundene Zahl als Einstand.
"""

from __future__ import annotations

from decimal import Decimal

from app.infrastructure.kickbase.dto import SquadResponseDTO

from tests.infrastructure.kickbase.vcr_config import (
    FAKE_LEAGUE_ID,
    FAKE_USER_ID,
    load_cassette_payload,
)

# `tty` in `/managers/{m}/transfer`: 1 = Zugang, 2 = Abgang. Empirisch aus der
# Cassette — Adam (12317) steht mit `tty=1` für 1.568.521 am 17.09. und mit
# `tty=2` für 899.208 am 22.09. im Log, also erst gekauft, dann mit Verlust
# abgegeben. Die umgekehrte Lesart ergäbe einen Verkauf vor dem Kauf.
_TRANSFER_TYPE_BUY = 1


def _latest_buy_prices() -> dict[str, Decimal]:
    """Letzter *Kauf*preis je Spieler aus der Transferhistorie.

    Die Liste ist absteigend nach Datum; der erste Treffer ist damit der
    jüngste Kauf — und nur der zählt, wenn ein Spieler mehrfach gehandelt
    wurde.
    """
    prices: dict[str, Decimal] = {}
    for entry in load_cassette_payload("manager_transfer")["it"]:
        if entry.get("tty") != _TRANSFER_TYPE_BUY:
            continue
        prices.setdefault(str(entry["pi"]), Decimal(str(entry["trp"])))
    return prices


def _squad():  # type: ignore[no-untyped-def]
    raw = load_cassette_payload("squad")
    return SquadResponseDTO.model_validate(raw).to_domain(FAKE_LEAGUE_ID, FAKE_USER_ID)


def test_buy_price_matches_the_transfer_history() -> None:
    """`mv - mvgl` muss dem bezahlten Preis aus `/transfer` entsprechen."""
    squad = _squad()
    transfers = _latest_buy_prices()

    checked = 0
    for sp in squad.players:
        expected = transfers.get(sp.player.id)
        if expected is None:
            continue
        assert sp.buy_price == expected, (
            f"{sp.player.last_name}: aus `mvgl` abgeleiteter Einstand {sp.buy_price} weicht vom "
            f"bezahlten Preis {expected} aus der Transferhistorie ab — die Annahme "
            "`mvgl = mv - prc` trägt dann nicht."
        )
        checked += 1

    assert checked >= 2, (
        "Weniger als zwei Kaderspieler in der Transferhistorie — die Gegenprobe wäre aussagelos. "
        "Cassette neu aufnehmen."
    )


def test_every_squad_player_has_a_buy_price() -> None:
    """Das DoD von P1-6: kein Kaderspieler ohne Einstand.

    Der Test misst genau, was Defekt D7 war: über den eigenen `trade_log`
    bekommen nur selbst gekaufte Spieler einen Einstand. In dieser Cassette
    stehen sechs der acht Kaderspieler **nicht** in der Transferhistorie — sie
    wurden zugelost. Über `mvgl` haben trotzdem alle acht einen.
    """
    squad = _squad()
    without = [sp.player.last_name for sp in squad.players if sp.buy_price is None]
    assert not without, f"Kaderspieler ohne Einstand: {without}"

    transfers = _latest_buy_prices()
    never_traded = [sp for sp in squad.players if sp.player.id not in transfers]
    assert never_traded, (
        "Cassette ohne zugelosten Spieler — dann prüft dieser Test nicht, was D7 war."
    )


def test_unrealized_pnl_is_consistent_with_the_derived_buy_price() -> None:
    """`mv - buy_price == mvgl` — die Umkehrung muss zurückführen.

    Hält diese Identität nicht, hat jemand den Buchgewinn neu berechnet statt
    ihn durchzureichen, und zwei Zahlen im Payload widersprechen sich.
    """
    raw_by_id = {str(item["pi"]): item for item in load_cassette_payload("squad")["it"]}
    for sp in _squad().players:
        raw_mvgl = raw_by_id[sp.player.id].get("mvgl")
        assert sp.unrealized_pnl == Decimal(str(raw_mvgl))
        assert sp.player.market_value - sp.buy_price == sp.unrealized_pnl
