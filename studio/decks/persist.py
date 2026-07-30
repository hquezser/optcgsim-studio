"""Écriture d'un deck vers le simulateur ET la base — un seul endroit.

Cette séquence (écrire le `.txt` natif, récupérer ou créer le profil par défaut, insérer la
ligne `decks`) existait en TROIS exemplaires : `StudioService._persist_deck`,
`cmd_decks_import` et `cmd_decks_import_pack`. Trois copies d'une même règle dérivent
toujours — c'était déjà commencé : l'isolation des échecs d'écriture n'avait été ajoutée
qu'à deux d'entre elles.

Le module vit sous `decks/` parce que la CLI et l'API l'importent déjà toutes les deux et
n'ont pas le droit de s'importer l'une l'autre. Il reçoit le `store` en paramètre plutôt que
de l'ouvrir : l'appelant maîtrise la transaction et peut enchaîner plusieurs decks.
"""

from __future__ import annotations

from pathlib import Path

from . import importer


def default_profile(store) -> dict:
    """Profil par défaut, créé au premier usage. Un studio neuf n'en a aucun."""
    profiles = store.list("profiles")
    return profiles[0] if profiles else store.put("profiles", {"name": "default",
                                                               "prefs": {}})


def persist_deck(store, deck: importer.Decklist, name: str, tags: list[str] | None,
                 persistent_dir: Path) -> Path:
    """Écrit le deck dans le sim puis en base. Renvoie le chemin du `.txt` écrit.

    L'ordre compte : si l'écriture du `.txt` échoue (dossier du sim non écrivable, nom
    impossible sur ce système de fichiers), on ne veut pas d'une ligne en base pointant vers
    un fichier inexistant.
    """
    chemin = deck.save_to_sim(name, persistent_dir)
    profil = default_profile(store)
    store.put("decks", {"profile_id": profil["id"], "name": name,
                        "leader": deck.leader, "cards": deck.cards,
                        "tags": list(tags or []), "source": deck.source})
    return chemin
