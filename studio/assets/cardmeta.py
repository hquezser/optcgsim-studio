"""Métadonnées de cartes minimales pour l'import sélectif (P7).

Ne porte qu'une seule information : « ce card_id est-il un Leader ? » — nécessaire au filtre
« leaders alternatifs uniquement ». La table (studio/assets/data/leaders.json) est extraite
du projet frère optcgsim-haki (card_stats.json) par scripts/refresh_leaders.py ; on ne
vendorise que les ids Leader (132), pas tout le référentiel.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).parent / "data" / "leaders.json"


@lru_cache(maxsize=1)
def leader_ids() -> frozenset[str]:
    try:
        return frozenset(json.loads(_DATA.read_text()).get("leaders", []))
    except (OSError, ValueError):
        return frozenset()


def is_leader(card_id: str) -> bool:
    """Vrai si `card_id` est une carte Leader. Défaut prudent : False si inconnu
    (une carte hors table ne sera pas prise pour un leader -> le filtre 'leaders only'
    n'inclut jamais une carte par erreur)."""
    return card_id in leader_ids()
