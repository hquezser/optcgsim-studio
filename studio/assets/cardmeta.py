"""Métadonnées de cartes minimales pour l'import sélectif par type (P7/P8).

Porte la correspondance card_id -> type (Leader / Character / Event / Stage), nécessaire aux
filtres « leaders uniquement », « événements uniquement », etc. La table
(studio/assets/data/card_types.json) est extraite du projet frère optcgsim-haki
(card_stats.json) par scripts/refresh_cardmeta.py ; on ne vendorise que id->type (~43 Ko).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "card_types.json"

# type long <-> lettre stockée
_LONG = {"L": "Leader", "C": "Character", "E": "Event", "S": "Stage"}
_SHORT = {v: k for k, v in _LONG.items()}
TYPES = ("Leader", "Character", "Event", "Stage")


@lru_cache(maxsize=1)
def _types() -> dict[str, str]:
    """card_id -> type long. Vide si la table est absente/corrompue (défaut prudent)."""
    try:
        raw = json.loads(_DATA.read_text()).get("types", {})
        return {cid: _LONG[s] for cid, s in raw.items() if s in _LONG}
    except (OSError, ValueError):
        return {}


def card_type(card_id: str) -> str | None:
    """Type d'une carte (Leader/Character/Event/Stage), ou None si inconnue."""
    return _types().get(card_id)


@lru_cache(maxsize=8)
def ids_of_type(type_name: str) -> frozenset[str]:
    """Ensemble des ids d'un type donné (insensible à la casse : 'event' == 'Event')."""
    want = type_name.strip().capitalize()
    return frozenset(cid for cid, t in _types().items() if t == want)


def leader_ids() -> frozenset[str]:
    return ids_of_type("Leader")


def is_leader(card_id: str) -> bool:
    """Vrai si `card_id` est une carte Leader. Défaut prudent : False si inconnu."""
    return card_type(card_id) == "Leader"
