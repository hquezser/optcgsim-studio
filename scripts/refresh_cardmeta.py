#!/usr/bin/env python3
"""Régénère studio/assets/data/card_types.json depuis card_stats.json du projet frère.

La table id→type (Leader/Character/Event/Stage) sert à l'import granulaire par type (P8) :
filtrer « leaders uniquement », « événements uniquement », etc. On ne vendorise QUE la
correspondance id→type (une lettre par carte, ~43 Ko pour 2558 cartes), pas tout
card_stats.json (930 Ko) : c'est la seule info dont le studio a besoin.

On en extrait aussi la (très courte) liste des cartes hors limite des 4 exemplaires : celles
dont le texte porte la clause « you may have any number of this card in your deck »
(Pacifista, Biscuit Warrior, Prisoner of Impel Down). Sans elle, l'import rejette à tort des
decklists de tournoi parfaitement légales.

Usage :
    python3 scripts/refresh_cardmeta.py [chemin/vers/card_stats.json]
Défaut : cherché dans plusieurs emplacements (cf. DEFAULT_CANDIDATES) — optcgsim-haki-public
vit HORS de l’écosystème optcgsim ; la dépendance est de maintenance seulement.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

_REL_SRC = Path("optcgsim-haki-public") / "optcgsim_haki" / "data" / "card_stats.json"
# `optcgsim-haki-public` ne fait PAS partie de l'écosystème optcgsim (studio, deckpacks,
# deckpacks-data, deckpacks-library) : il vit à côté. La dépendance est de MAINTENANCE
# seulement — card_types.json est vendorisé et commité, donc le studio tourne sans lui, et
# seule la régénération de la table le réclame. D'où plusieurs emplacements plausibles
# essayés dans l'ordre, plutôt qu'un chemin unique codé en dur qui casse au moindre
# rangement de dossiers.
_ECOSYSTEM = Path(__file__).resolve().parent.parent.parent
DEFAULT_CANDIDATES = (
    _ECOSYSTEM / _REL_SRC,                                       # voisin dans l'écosystème
    _ECOSYSTEM.parent / "draft-optcgsim-projects" / _REL_SRC,     # projets de brouillon
    _ECOSYSTEM.parent / _REL_SRC,                                # à côté de l'écosystème
)
OUT = Path(__file__).resolve().parent.parent / "studio" / "assets" / "data" / "card_types.json"
_SHORT = {"Leader": "L", "Character": "C", "Event": "E", "Stage": "S"}
# Clause officielle des cartes sans limite de copies, telle qu'imprimée sur la carte.
_UNLIMITED_RE = re.compile(r"any number of this card in your deck", re.I)


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    src = Path(argv[0]) if argv else next(
        (c for c in DEFAULT_CANDIDATES if c.exists()), None)
    if src is None:
        print("Source introuvable. Emplacements essayés :")
        for c in DEFAULT_CANDIDATES:
            print(f"  · {c}")
        print("\nPassez le chemin en argument, ou récupérez card_stats.json depuis "
              "optcgsim-haki-public (hors écosystème).")
        return 1
    if not src.exists():
        print(f"Source introuvable : {src}")
        return 1
    cards = json.loads(src.read_text()).get("cards", {})
    m = {cid: _SHORT[c["card_type"]] for cid, c in cards.items()
         if isinstance(c, dict) and c.get("card_type") in _SHORT}
    unlimited = sorted(cid for cid, c in cards.items()
                       if isinstance(c, dict) and _UNLIMITED_RE.search(c.get("text") or ""))
    OUT.write_text(json.dumps(
        {"_note": "id -> type (L/C/E/S) + ids sans limite de copies, extraits de "
                  "optcgsim-haki/card_stats.json ; régénérer via scripts/refresh_cardmeta.py",
         "types": m, "unlimited": unlimited},
        separators=(",", ":")))
    print(f"{len(m)} cartes écrites dans {OUT} — {dict(Counter(m.values()))}")
    print(f"{len(unlimited)} carte(s) sans limite de copies : {', '.join(unlimited) or '—'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
