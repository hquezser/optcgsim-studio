"""Pack de decks — importer une COLLECTION nommée de decklists d'un coup (P6).

Cas d'usage : « Meta OP16 », « Rogue decks de Trecore »… au lieu d'importer deck par deck.

Format `deckpack.json` (propre au studio — pas de scraping de tier-list, même principe que
importer.py : les pages changent, un manifeste versionné non) :

    {
      "name": "Meta OP16",
      "author": "Trecore",
      "decks": [
        {"name": "Sanji Red", "tags": ["meta","op16"], "text": "1xPRB01-001\\n4x..."},
        {"name": "Rogue Zoro", "tags": ["rogue"], "file": "decks/zoro.txt"},
        {"name": "Kid",        "tags": ["meta"],  "source_url": "https://..."}
      ]
    }

Chaque deck est résolu par le moteur d'import EXISTANT (parse_text / from_url), sans
modification. Un deck en échec n'interrompt JAMAIS les autres : il ressort dans `failed`.

Ce module ne fait que RÉSOUDRE (produire des Decklist) ; l'écriture vers le sim et la base
est du ressort de l'appelant (service API / CLI), qui réutilise sa logique d'import unitaire.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import importer


class DeckPackError(Exception):
    pass


# Version du format deckpack.json comprise par ce studio. Un pack peut déclarer
# `"schema_version": N` ; on résout au mieux et on AVERTIT si N est plus récent
# (compat ascendante : on n'échoue pas sur un pack publié par une version future).
SCHEMA_VERSION = 1


@dataclass
class ResolvedDeck:
    name: str
    tags: list[str]
    deck: importer.Decklist


@dataclass
class DeckPackReport:
    name: str
    author: str | None = None
    imported: list[ResolvedDeck] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)      # {name, reason}
    warnings: list[str] = field(default_factory=list)     # non bloquant (version, méta)

    def summary(self) -> str:
        s = f"« {self.name} » : {len(self.imported)} deck(s) importé(s)"
        if self.failed:
            s += f", {len(self.failed)} en échec"
        if self.warnings:
            s += f", {len(self.warnings)} avertissement(s)"
        return s


def find_manifest(pack_dir: Path) -> Path:
    """Localise deckpack.json (racine, ou un seul niveau d'emballage — cas zip/URL)."""
    pack_dir = Path(pack_dir)
    direct = pack_dir / "deckpack.json"
    if direct.exists():
        return direct
    hits = list(pack_dir.glob("*/deckpack.json"))
    if len(hits) == 1:
        return hits[0]
    raise DeckPackError("deckpack.json introuvable dans la source "
                        "(attendu à la racine du dossier/zip).")


def resolve(manifest: dict, pack_dir: Path,
            from_url: Callable[[str, str | None], importer.Decklist] | None = None
            ) -> DeckPackReport:
    """Résout chaque entrée du manifeste en Decklist. `from_url` injectable (tests hors-réseau)."""
    from_url = from_url or importer.from_url
    pack_dir = Path(pack_dir)
    entries = manifest.get("decks")
    if not isinstance(entries, list) or not entries:
        raise DeckPackError("Manifeste sans liste `decks` non vide.")
    rep = DeckPackReport(name=manifest.get("name") or "Deck pack",
                         author=manifest.get("author"))
    ver = manifest.get("schema_version", 1)
    if isinstance(ver, int) and ver > SCHEMA_VERSION:
        rep.warnings.append(
            f"pack au format v{ver}, ce studio comprend v{SCHEMA_VERSION} — "
            "résolution au mieux, mets à jour le studio pour le support complet.")
    seen_names: set[str] = set()
    for i, entry in enumerate(entries):
        name = (entry.get("name") if isinstance(entry, dict) else None) or f"deck {i + 1}"
        try:
            if not isinstance(entry, dict):
                raise DeckPackError("entrée de deck invalide (objet attendu)")
            tags = entry.get("tags") or []
            if "text" in entry:
                deck = importer.parse_text(entry["text"], name=name, source="deckpack")
            elif "file" in entry:
                fp = (pack_dir / entry["file"])
                if not fp.is_file() or ".." in Path(entry["file"]).parts:
                    raise DeckPackError(f"fichier introuvable/illégal : {entry['file']}")
                deck = importer.parse_text(fp.read_text(errors="ignore"), name=name,
                                           source=f"deckpack:{entry['file']}")
            elif "source_url" in entry:
                deck = from_url(entry["source_url"], name)
            else:
                raise DeckPackError("ni `text`, ni `file`, ni `source_url`")
            # Provenance = le PACK, pas le fichier/l'URL interne (remplace la valeur posée par
            # parse_text/from_url) — pour pouvoir répondre à « importé depuis quel pack ? ».
            deck.source = f"deckpack:{rep.name}"
            # nom unique dans le pack (évite d'écraser deux decks homonymes)
            uniq, n = name, 2
            while uniq in seen_names:
                uniq, n = f"{name} ({n})", n + 1
            seen_names.add(uniq)
            rep.imported.append(ResolvedDeck(name=uniq, tags=list(tags), deck=deck))
        except (importer.ImportError_, DeckPackError, OSError) as e:
            rep.failed.append({"name": name, "reason": str(e)})
    return rep


def generate(name: str, decks: list[ResolvedDeck], author: str | None = None) -> dict:
    """Construit un `deckpack.json` (P6) à partir de decks déjà résolus — inverse exact de
    `resolve()` : réutilise `Decklist.to_native_text()`, aucune nouvelle sérialisation."""
    return {
        "name": name,
        "author": author,
        "schema_version": SCHEMA_VERSION,
        "decks": [{"name": rd.name, "tags": rd.tags, "text": rd.deck.to_native_text()}
                  for rd in decks],
    }


def from_source(source: str | Path, ingest, work_dir: Path,
                from_url: Callable | None = None) -> DeckPackReport:
    """Bout-en-bout : ingère une source (via `ingest`, = packlib.ingest) puis résout.

    `ingest` est injecté pour ne pas coupler decks/ à assets/packlib (et faciliter les tests).
    """
    import shutil
    try:
        src = ingest(source, work_dir)
        manifest = json.loads(find_manifest(src).read_text())
        return resolve(manifest, find_manifest(src).parent, from_url=from_url)
    finally:
        if Path(work_dir).exists():
            shutil.rmtree(work_dir, ignore_errors=True)
