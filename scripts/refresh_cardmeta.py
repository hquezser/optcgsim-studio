#!/usr/bin/env python3
"""Régénère studio/assets/data/card_types.json depuis card_stats.json du projet frère.

La table id→type (Leader/Character/Event/Stage) sert à l'import granulaire par type (P8) :
filtrer « leaders uniquement », « événements uniquement », etc. On ne vendorise QUE la
correspondance id→type (une lettre par carte, ~43 Ko pour 2558 cartes), pas tout
card_stats.json (930 Ko) : c'est la seule info dont le studio a besoin.

Usage :
    python3 scripts/refresh_cardmeta.py [chemin/vers/card_stats.json]
Défaut : ../optcgsim-haki-public/optcgsim_haki/data/card_stats.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_SRC = (Path(__file__).resolve().parent.parent.parent
               / "optcgsim-haki-public" / "optcgsim_haki" / "data" / "card_stats.json")
OUT = Path(__file__).resolve().parent.parent / "studio" / "assets" / "data" / "card_types.json"
_SHORT = {"Leader": "L", "Character": "C", "Event": "E", "Stage": "S"}


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    src = Path(argv[0]) if argv else DEFAULT_SRC
    if not src.exists():
        print(f"Source introuvable : {src}")
        return 1
    cards = json.loads(src.read_text()).get("cards", {})
    m = {cid: _SHORT[c["card_type"]] for cid, c in cards.items()
         if isinstance(c, dict) and c.get("card_type") in _SHORT}
    OUT.write_text(json.dumps(
        {"_note": "id -> type (L/C/E/S), extrait de optcgsim-haki/card_stats.json ; "
                  "régénérer via scripts/refresh_cardmeta.py", "types": m},
        separators=(",", ":")))
    print(f"{len(m)} cartes écrites dans {OUT} — {dict(Counter(m.values()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
