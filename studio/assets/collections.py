"""collections.py — manifeste décrivant un GROUPE de dépôts de packs liés (P10).

Contexte : `repos build` (P8+) produit plusieurs dépôts par FAMILLE (cards-alt, cardbacks,
playmats, translated-fr-classic, translated-fr-fullart, translations-fr…) pour un seul
« look » cohérent. Importer ce groupe via l'UI web demandait jusqu'ici un aller-retour manuel
par dépôt, sans rien qui explique que deux familles peuvent être des VARIANTES du même choix
(ex. cartes classiques vs full-art — un seul à retenir) plutôt que des compléments (traduction,
tapis, dos — tous à ajouter). Voir docs/PLAN-import-packs.md, chantier P10, pour le plan complet.

Format `collection.json` :
    {
      "schema_version": 1,
      "name": "FR classique + full-art",
      "packs": [
        {"family": "translated-fr-classic", "url": "https://github.com/…", "label": "…",
         "variant_group": "cards"},
        {"family": "translated-fr-fullart", "url": "https://github.com/…", "label": "…",
         "variant_group": "cards"},
        {"family": "translations-fr", "url": "https://github.com/…", "label": "Traduction FR"}
      ]
    }

`family` est écrit par `repobuild` (clé d'upsert, cf. `repobuild._upsert_collection_entry`)
mais n'est pas requis pour CONSOMMER le manifeste — seuls `url`/`label`/`variant_group`
comptent ici. Deux entrées partageant le même `variant_group` sont des ALTERNATIVES (un seul
choix, présenté en radio côté UI) ; une entrée SANS `variant_group` est COMPLÉMENTAIRE (case
à cocher, cochée par défaut).

Ce module ne fait QUE parser/valider un manifeste déjà obtenu (dict local, ou une chaîne
JSON) — il ne télécharge rien lui-même (contrairement à `repobuild`/`sourcefetch`) et ne parle
pas au réseau. La RÉSOLUTION distante (fetch d'une URL de manifeste, cf. plan P10-c,
`POST /api/collections/resolve`, PAS ENCORE IMPLÉMENTÉE) est un TODO côté API ; ce module sert
de base commune au CLI (génération) et à la future route API (consommation).

Import réel d'une collection choisie (PAS ENCORE IMPLÉMENTÉ, cf. plan P10-c) : réutilise
`packlib.add_pack` PACK PAR PACK (une invocation par entrée sélectionnée) — aucune nouvelle
route d'ajout n'est nécessaire, ce module ne fait qu'aider à PRÉSENTER le choix (radio/checkbox).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# Version du format collection.json comprise par ce studio. Même politique que
# deckpack.SCHEMA_VERSION (P9) : une version future est acceptée au mieux, avec avertissement,
# jamais un échec dur (compat ascendante — un manifeste publié doit rester important-able
# par un studio plus ancien).
SCHEMA_VERSION = 1


class CollectionError(Exception):
    pass


@dataclass
class CollectionPack:
    url: str
    label: str
    variant_group: str | None = None
    family: str | None = None   # métadonnée d'origine (repobuild) ; ignorée à la consommation


@dataclass
class Collection:
    name: str
    packs: list[CollectionPack] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def variant_groups(self) -> dict[str, list[CollectionPack]]:
        """{clé de groupe: [packs]} — les entrées à choix ALTERNATIF (radio côté UI) :
        un seul pack de chaque groupe doit être importé, pas tous."""
        groups: dict[str, list[CollectionPack]] = {}
        for p in self.packs:
            if p.variant_group:
                groups.setdefault(p.variant_group, []).append(p)
        return groups

    def standalone_packs(self) -> list[CollectionPack]:
        """Entrées COMPLÉMENTAIRES (checkbox, cochées par défaut) — sans `variant_group`,
        donc jamais en concurrence avec une autre entrée de la collection."""
        return [p for p in self.packs if not p.variant_group]

    def summary(self) -> str:
        groups = self.variant_groups()
        s = f"« {self.name} » : {len(self.packs)} pack(s)"
        if groups:
            s += f", {len(groups)} groupe(s) de variantes ({', '.join(sorted(groups))})"
        if self.warnings:
            s += f", {len(self.warnings)} avertissement(s)"
        return s


def parse(data: dict) -> Collection:
    """Parse + valide un manifeste `collection.json` déjà chargé (dict Python, pas une chaîne).

    Lève `CollectionError` si structurellement invalide (pas de liste `packs` non vide, ou une
    entrée sans `url`). Avertit (n'échoue PAS) si `schema_version` est plus récent que ce que
    ce studio comprend — même politique que `deckpack.resolve` (P9) : un manifeste publié par
    un studio futur doit rester résolu au mieux, pas rejeté."""
    if not isinstance(data, dict):
        raise CollectionError("Manifeste invalide (objet JSON attendu).")
    if not isinstance(data.get("packs"), list) or not data["packs"]:
        raise CollectionError("Manifeste sans liste `packs` non vide.")

    col = Collection(name=data.get("name") or "Collection")
    ver = data.get("schema_version", 1)
    if isinstance(ver, int) and ver > SCHEMA_VERSION:
        col.warnings.append(
            f"collection au format v{ver}, ce studio comprend v{SCHEMA_VERSION} — "
            "résolution au mieux, mets à jour le studio pour le support complet.")

    for i, entry in enumerate(data["packs"]):
        if not isinstance(entry, dict) or not entry.get("url"):
            raise CollectionError(f"packs[{i}] : entrée invalide (`url` requise et non vide).")
        col.packs.append(CollectionPack(
            url=entry["url"],
            label=entry.get("label") or entry["url"],
            variant_group=entry.get("variant_group") or None,
            family=entry.get("family"),
        ))
    return col


def parse_text(text: str) -> Collection:
    """`parse()` à partir d'une chaîne JSON brute (fichier local lu, ou réponse HTTP déjà
    récupérée côté appelant — ce module ne fait lui-même aucun accès réseau)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CollectionError(f"JSON invalide : {e}") from e
    return parse(data)


def load(path: Path | str) -> Collection:
    """Charge depuis un fichier local (déjà téléchargé/uploadé — pas de résolution d'URL ici)."""
    return parse_text(Path(path).read_text())
