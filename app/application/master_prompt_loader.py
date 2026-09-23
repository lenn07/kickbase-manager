"""Lädt den statischen System-Prompt aus `docs/master_prompt.md`.

Der Master-Prompt-Text (Abschnitte 1 bis 8) ist absichtlich in einer
Markdown-Datei gepflegt, damit er ohne Code-Änderung überarbeitet werden
kann. Zur Laufzeit extrahieren wir den Text zwischen dem `## SYSTEM`- und
`## USER`-Header - das ist der Teil, der als System-Prompt an Claude geht
und via Prompt-Caching einmal pro Cache-Fenster bezahlt werden muss.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "docs" / "master_prompt.md"

_SYSTEM_HEADER = "## SYSTEM"
_USER_HEADER = "## USER"

# Muss synchron zur Wartungshinweis-Zeile in `docs/master_prompt.md` gehalten
# werden. Sobald der Master-Prompt gegen neue Kickbase-Regeln geprüft wurde,
# hier das Datum aktualisieren — der AI-Only-Modus liefert es via USER-JSON
# als `rules_last_verified` an das LLM (§ 8 des Master-Prompts).
RULES_LAST_VERIFIED: date = date(2026, 9, 23)


class MasterPromptError(RuntimeError):
    """Datei fehlt oder Markdown-Struktur passt nicht."""


def load_system_prompt(path: Path | None = None) -> str:
    """Liest die Markdown-Datei und gibt den reinen System-Text zurück.

    Wirft `MasterPromptError`, wenn die Datei fehlt oder die Marker
    (`## SYSTEM` / `## USER`) nicht im Dokument vorkommen.
    """
    prompt_path = path or _DEFAULT_PATH
    try:
        raw = prompt_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise MasterPromptError(f"Master-Prompt fehlt: {prompt_path}") from exc

    system_idx = raw.find(_SYSTEM_HEADER)
    user_idx = raw.find(_USER_HEADER)
    if system_idx < 0 or user_idx < 0 or user_idx <= system_idx:
        raise MasterPromptError(
            f"Marker `{_SYSTEM_HEADER}` / `{_USER_HEADER}` nicht in Reihenfolge in {prompt_path}."
        )

    body = raw[system_idx + len(_SYSTEM_HEADER) : user_idx]
    return _strip_section_separators(body).strip()


@lru_cache(maxsize=1)
def get_cached_system_prompt() -> str:
    """Cached-Variante — der Master-Prompt ändert sich zur Laufzeit nicht."""
    return load_system_prompt()


def _strip_section_separators(text: str) -> str:
    """Entfernt `---`-Trenner-Zeilen (Markdown-Formatting, kein Prompt-Inhalt).

    Leerzeilen bleiben erhalten — sie strukturieren den Prompt visuell.
    Nur reine Bindestrich-Zeilen (`---`, `----`, …) fallen raus.
    """
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and set(stripped) == {"-"}:
            continue
        kept.append(line)
    return "\n".join(kept)


__all__ = [
    "RULES_LAST_VERIFIED",
    "MasterPromptError",
    "get_cached_system_prompt",
    "load_system_prompt",
]
