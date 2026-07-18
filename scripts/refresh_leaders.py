#!/usr/bin/env python3
"""Régénère studio/assets/data/leaders.json depuis card_stats.json du projet frère.

La liste des ids Leader sert à l'import sélectif « leaders alternatifs uniquement » (P7).
On ne vendorise QUE les ids (132 entrées, ~1,6 Ko) plutôt que tout card_stats.json (930 Ko) :
c'est la seule information dont le studio a besoin, et ça reste lisible/diffable.

Usage :
    python3 scripts/refresh_leaders.py [chemin/vers/card_stats.json]
Défaut : ../optcgsim-haki-public/optcgsim_haki/data/card_stats.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_SRC = (Path(__file__).resolve().parent.parent.parent
               / "optcgsim-haki-public" / "optcgsim_haki" / "data" / "card_stats.json")
OUT = Path(__file__).resolve().parent.parent / "studio" / "assets" / "data" / "leaders.json"


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    src = Path(argv[0]) if argv else DEFAULT_SRC
    if not src.exists():
        print(f"Source introuvable : {src}")
        return 1
    cards = json.loads(src.read_text()).get("cards", {})
    leaders = sorted(cid for cid, c in cards.items()
                     if isinstance(c, dict) and c.get("card_type") == "Leader")
    OUT.write_text(json.dumps(
        {"_note": "ids des cartes Leader, extraits de optcgsim-haki/data/card_stats.json ; "
                  "régénérer avec scripts/refresh_leaders.py",
         "leaders": leaders}, indent=0, ensure_ascii=False))
    print(f"{len(leaders)} leaders écrits dans {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
