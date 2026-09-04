"""Mapping Kickbase-`team_id` → OpenLigaDB-Team-Namens-Aliase.

Kickbase-team_ids sind global stabile Bundesliga-Klub-IDs. OpenLigaDB liefert
klarnamen wie „FC Bayern München" oder „Borussia Dortmund". Wir matchen über
mehrere Namensvarianten (voller Name, Kurzname, Alternative), damit
Saison-Umbenennungen tolerant sind. Teams, die nicht gelistet sind
(Aufsteiger, unbekannte IDs), werden vom Adapter als neutral behandelt.
"""

from __future__ import annotations

_KICKBASE_TEAM_ALIASES: dict[str, tuple[str, ...]] = {
    "2": ("Borussia Dortmund", "BVB", "Dortmund"),
    "3": ("Eintracht Frankfurt", "SGE", "Frankfurt"),
    "4": ("SC Freiburg", "Freiburg"),
    "5": ("Hamburger SV", "Hamburg", "HSV"),
    "7": ("Bayer 04 Leverkusen", "Leverkusen", "Bayer Leverkusen"),
    "9": ("VfL Wolfsburg", "Wolfsburg"),
    "10": ("FC Bayern München", "Bayern München", "Bayern Munich", "FCB"),
    "11": ("VfB Stuttgart", "Stuttgart"),
    "13": ("FC Augsburg", "Augsburg"),
    "14": ("TSG Hoffenheim", "TSG 1899 Hoffenheim", "Hoffenheim"),
    "15": ("1. FSV Mainz 05", "Mainz 05", "Mainz"),
    "18": ("Borussia Mönchengladbach", "Mönchengladbach", "Gladbach"),
    "20": ("SV Werder Bremen", "Werder Bremen", "Bremen"),
    "24": ("FC St. Pauli", "St. Pauli", "St Pauli"),
    "28": ("1. FC Köln", "FC Köln", "Köln"),
    "39": ("1. FC Union Berlin", "Union Berlin", "Union"),
    "40": ("RB Leipzig", "Leipzig"),
    "43": ("1. FC Heidenheim 1846", "Heidenheim", "1. FC Heidenheim"),
    "50": ("Hertha BSC", "Hertha", "Hertha Berlin"),
    "51": ("VfL Bochum", "Bochum"),
}


def kickbase_team_aliases(team_id: str) -> tuple[str, ...]:
    """Liefert bekannte Namensvarianten für eine Kickbase-team_id.

    Leeres Tupel bedeutet: Team unbekannt, kein Match möglich.
    """
    return _KICKBASE_TEAM_ALIASES.get(team_id, ())


def _normalize(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum() or ch.isspace()).strip()


def build_team_index(kickbase_team_ids: list[str]) -> dict[str, str]:
    """Baut eine Reverse-Lookup-Map: normalisierter Team-Name → Kickbase-team_id.

    Nur Teams, für die wir Aliase kennen, landen im Index. Damit kann der
    Adapter beim Iterieren über OpenLigaDB-Matches direkt auf die Kickbase-ID
    zurückschlagen.
    """
    index: dict[str, str] = {}
    for kb_id in kickbase_team_ids:
        for alias in kickbase_team_aliases(kb_id):
            index[_normalize(alias)] = kb_id
    return index


def match_opendb_team_name(opendb_name: str, index: dict[str, str]) -> str | None:
    """Findet zu einem OpenLigaDB-Namen die passende Kickbase-team_id.

    Direkter Normalisierungs-Match zuerst, dann Substring-Fallback (der
    OpenLigaDB-Klarname enthält oft einen unserer Aliase, z. B.
    „FC Bayern München" ⊃ „Bayern München"). Findet nichts → None.
    """
    if not opendb_name:
        return None
    normalized = _normalize(opendb_name)
    if normalized in index:
        return index[normalized]
    for alias_norm, kb_id in index.items():
        if alias_norm and alias_norm in normalized:
            return kb_id
    return None
