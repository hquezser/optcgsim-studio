"""slots — les EMPLACEMENTS fixes du jeu, et le choix EXPLICITE d'une image pour chacun.

Un pack ne peut que re-skinner de l'existant : `AssetManager.apply_mirror` n'écrit jamais un
fichier inconnu du jeu (garde-fou volontaire). Or un dépôt d'alt-arts propose couramment N
images pour UN emplacement unique — 143 DON pour le seul `Cards/Don/Don.png`, des dizaines de
tapis pour 22 slots. Le NOM DE FICHIER décidait donc tout seul : celui qui s'appelait
littéralement `Don.png` gagnait, les 142 autres étaient écartés en silence (« aucune cible
correspondante dans le jeu ») et l'utilisateur n'avait aucun moyen de dire lequel il voulait.

Ce module comble ce trou. Il :
  1. NOMME les emplacements réels de l'installation (`list_slots`) ;
  2. liste les CANDIDATS que la bibliothèque de packs propose pour chacun (`candidates`) ;
  3. MÉMORISE le choix de l'utilisateur (`SlotChoices`) pour qu'il survive à la
     ré-application d'un pack — sans quoi le prochain « Appliquer » écraserait en silence
     une décision délibérée, exactement le comportement invisible qu'on corrige ici.

Aucune écriture dans le jeu ici : le swap passe par `AssetManager` (backup pristine, magic
bytes, atomicité, manifeste), seul détenteur des garde-fous.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from ..gamepaths import GameInstall
from . import packlib
from .manager import AssetError, AssetManager, image_info

IMAGE_EXT = (".png", ".jpg", ".jpeg")

# Préfixe de `source` dans le manifeste du manager. Distinct de `pack:<nom>` exprès : un
# choix d'emplacement est une décision de l'utilisateur, pas la propriété d'un pack — retirer
# le pack d'où venait l'image ne doit pas défaire le choix (le fichier est déjà écrit dans le
# jeu, et son original pristine reste sauvegardé de toute façon).
ORIGIN_PREFIX = "slot:"


@dataclass(frozen=True)
class Slot:
    """Un emplacement remplaçable du jeu. `rel` est relatif à StreamingAssets."""
    id: str          # "don", "playmat:Red", "cardback:regular", "background:main"
    kind: str        # catégorie packlib correspondante : don/playmats/cardbacks/backgrounds
    label: str       # libellé d'affichage
    rel: str         # chemin relatif dans StreamingAssets
    group: str       # regroupement d'affichage ("Cartes", "Tapis", …)

    @property
    def origin(self) -> str:
        return ORIGIN_PREFIX + self.id


def list_slots(install: GameInstall) -> list[Slot]:
    """Les emplacements RÉELLEMENT présents dans cette installation.

    On n'invente rien : un slot n'existe que si son fichier existe déjà dans le jeu — même
    règle que `apply_mirror`, donc un slot listé est toujours applicable.
    """
    sa = install.streaming_assets
    out: list[Slot] = []

    don = install.cards_dir / "Don" / "Don.png"
    if don.is_file():
        out.append(Slot("don", "don", "DON!!", "Cards/Don/Don.png", "Cartes"))

    for name in ("CardBackRegular", "CardBackDon"):
        if (install.cardbacks_dir / f"{name}.png").is_file():
            sid = "cardback:don" if name.endswith("Don") else "cardback:regular"
            label = "Dos de DON" if name.endswith("Don") else "Dos de carte"
            out.append(Slot(sid, "cardbacks", label, f"CardBacks/{name}.png", "Cartes"))

    for fname, sid, label in (("background.jpg", "background:main", "Fond principal"),
                              ("deckeditbackground.jpg", "background:deck_editor",
                               "Fond deck builder")):
        if (sa / fname).is_file():
            out.append(Slot(sid, "backgrounds", label, fname, "Fonds"))

    # Tapis : un slot par couleur/combinaison présente. `.png` seulement — `Playsheet.jpg`
    # cohabite dans le même dossier mais n'est pas un tapis (et `apply_playmat` exige un PNG).
    if install.playmats_dir.exists():
        for p in sorted(install.playmats_dir.glob("*.png")):
            out.append(Slot(f"playmat:{p.stem}", "playmats", p.stem,
                            f"Playmats/{p.name}", "Tapis"))
    return out


def find_slot(install: GameInstall, slot_id: str) -> Slot:
    slot = next((s for s in list_slots(install) if s.id == slot_id), None)
    if slot is None:
        raise KeyError(slot_id)
    return slot


# --------------------------------------------------------------------------- candidats
def candidates(lib_dir: Path, kind: str) -> list[dict]:
    """Toutes les images de la bibliothèque qui peuvent occuper un emplacement de ce `kind`.

    La classification réutilise `packlib.classify_rel` — la MÊME fonction que l'import — pour
    que « ce que le filtre appelle don » et « ce que le sélecteur propose comme DON » ne
    puissent jamais diverger.

    Les dimensions ne sont pas lues ici (un `image_info` par fichier sur 143 candidats coûte
    143 ouvertures pour une information que l'UI n'affiche pas) : `AssetManager` valide de
    toute façon l'image au moment du swap, et refuse proprement ce qui ne convient pas.
    """
    lib_dir = Path(lib_dir)
    if not lib_dir.is_dir():
        return []
    out: list[dict] = []
    for pack_dir in sorted(p for p in lib_dir.iterdir() if p.is_dir()):
        for f in sorted(pack_dir.rglob("*")):
            if f.is_symlink() or not f.is_file() or f.suffix.lower() not in IMAGE_EXT:
                continue
            rel = f.relative_to(pack_dir).as_posix()
            if packlib.classify_rel(rel)[0] != kind:
                continue
            out.append({"pack": pack_dir.name, "rel": rel, "name": f.stem,
                        "bytes": f.stat().st_size})
    return out


def candidate_counts(lib_dir: Path) -> dict[str, int]:
    """Nombre de candidats par `kind` — en UNE passe sur la bibliothèque.

    L'UI affiche ce compte sur chaque emplacement ; appeler `candidates()` une fois par
    catégorie re-parcourrait toute la bibliothèque autant de fois.
    """
    lib_dir = Path(lib_dir)
    counts = {"don": 0, "playmats": 0, "cardbacks": 0, "backgrounds": 0}
    if not lib_dir.is_dir():
        return counts
    for pack_dir in (p for p in lib_dir.iterdir() if p.is_dir()):
        for f in pack_dir.rglob("*"):
            if f.is_symlink() or not f.is_file() or f.suffix.lower() not in IMAGE_EXT:
                continue
            cat = packlib.classify_rel(f.relative_to(pack_dir).as_posix())[0]
            if cat in counts:
                counts[cat] += 1
    return counts


def resolve_candidate(lib_dir: Path, pack: str, rel: str) -> Path:
    """Chemin d'un candidat, en GARANTISSANT qu'il reste sous la bibliothèque.

    `pack` et `rel` viennent d'une requête HTTP : sans ce contrôle, `rel = "../../.ssh/id_rsa"`
    ferait servir puis copier un fichier arbitraire dans le jeu. Même raisonnement que
    `packlib._pack_dir_for` — on compare des CHEMINS RÉSOLUS, jamais des chaînes.
    """
    lib = Path(lib_dir).resolve()
    target = (lib / pack / rel)
    try:
        resolved = target.resolve()
    except OSError as e:                                  # chemin invalide (nom trop long…)
        raise AssetError(f"Candidat introuvable : {pack}/{rel}") from e
    if not resolved.is_relative_to(lib) or resolved == lib:
        raise AssetError(f"Chemin hors de la bibliothèque : {pack}/{rel}")
    if target.is_symlink() or not resolved.is_file():
        raise AssetError(f"Candidat introuvable : {pack}/{rel}")
    if resolved.suffix.lower() not in IMAGE_EXT:
        raise AssetError(f"Ce n'est pas une image : {pack}/{rel}")
    return resolved


# --------------------------------------------------------------------------- choix persistés
class SlotChoices:
    """Les choix d'emplacement de l'utilisateur, persistés dans `<state_dir>/slots.json`.

    Séparé du manifeste du manager (qui décrit des SWAPS, état factuel du jeu) : ceci décrit
    une INTENTION (« je veux ce DON-là »), qui doit survivre à une ré-application de pack et
    reste vraie même si une mise à jour du sim a entre-temps écrasé le fichier.
    """

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "slots.json"

    def all(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def set(self, slot_id: str, path: Path, pack: str | None, rel: str | None) -> None:
        data = self.all()
        data[slot_id] = {"path": str(path), "pack": pack, "rel": rel}
        self._save(data)

    def clear(self, slot_id: str) -> None:
        data = self.all()
        if data.pop(slot_id, None) is not None:
            self._save(data)


# --------------------------------------------------------------------------- application
def apply_choice(mgr: AssetManager, slot: Slot, image: Path) -> list[Path]:
    """Écrit `image` dans l'emplacement `slot`. Renvoie les cibles réellement remplacées.

    Chaque famille passe par sa méthode dédiée du manager plutôt que par un `_swap` direct :
    ce sont elles qui portent les contrôles propres à la famille (gabarit de carte, PNG exigé
    pour un tapis ou un dos, JPEG pour un fond) et, pour le DON, la couverture de TOUTES ses
    occurrences (image de base + miniature + caches versionnés).
    """
    image = Path(image)
    origin = slot.origin
    if slot.kind == "don":
        return mgr.apply_card("Don", image, origin=origin)
    if slot.kind == "playmats":
        return [mgr.apply_playmat(slot.rel.split("/")[-1][:-4], image, origin=origin)]
    if slot.kind == "cardbacks":
        return [mgr.apply_cardback(image, don=slot.id.endswith(":don"), origin=origin)]
    if slot.kind == "backgrounds":
        return [mgr.apply_background(image, deck_editor=slot.id.endswith(":deck_editor"),
                                     origin=origin)]
    raise AssetError(f"Emplacement inconnu : {slot.id}")


def reapply_choices(install: GameInstall, mgr: AssetManager,
                    applied_rels: list[str]) -> list[str]:
    """Ré-impose les choix d'emplacement qu'une application de pack vient d'écraser.

    Sans ceci, « appliquer » repose le `Don.png` du pack par-dessus le DON choisi à la main,
    EN SILENCE — la panne exacte que ce module existe pour corriger. L'invariant est donc :
    un choix délibéré survit à toute application de pack, quel que soit le chemin de code.

    Cette fonction vit ici et non dans le serveur précisément pour cette raison. Elle y a
    d'abord été écrite, et le CLI — quatre points d'application — ne l'appelait pas : le
    sélecteur tenait dans l'interface web et pas en ligne de commande. C'est la même famille
    de défaut que la divergence `classify_rel` corrigée juste à côté, où l'import et
    l'application classaient les chemins avec deux fonctions qui ne disaient plus la même
    chose. Une règle, un seul endroit.

    On ne ré-applique QUE les emplacements que ce pack vient de toucher : réécrire les autres
    serait des écritures dans le bundle du jeu pour rien.
    """
    touched = set(applied_rels)
    choices = SlotChoices(mgr.state_dir).all()
    if not choices:
        return []
    redone: list[str] = []
    for slot in list_slots(install):
        choice = choices.get(slot.id)
        if choice is None or slot.rel not in touched:
            continue
        image = Path(choice["path"])
        if not image.is_file():
            continue              # source disparue (pack retiré) : le swap en place reste
        try:
            apply_choice(mgr, slot, image)
            redone.append(slot.label)
        except AssetError:
            continue              # image devenue invalide : le pack garde la main
    return redone


def describe(install: GameInstall, mgr: AssetManager, lib_dir: Path) -> list[dict]:
    """État complet des emplacements, prêt pour l'UI (JSON)."""
    counts = candidate_counts(lib_dir)
    choices = SlotChoices(mgr.state_dir).all()
    manifest = mgr._manifest                       # lecture seule : compte des swaps par cible
    out = []
    for s in list_slots(install):
        target = str((install.streaming_assets / s.rel).resolve())
        entry = manifest.get(target)
        choice = choices.get(s.id)
        out.append({
            "id": s.id, "kind": s.kind, "label": s.label, "rel": s.rel, "group": s.group,
            "candidates": counts.get(s.kind, 0),
            # « modifié » = le jeu ne contient plus son fichier d'origine, quelle qu'en soit
            # la cause (un pack miroir, ou un choix explicite).
            "swapped": entry is not None,
            "source": (entry or {}).get("source"),
            # `choice` n'est renseigné que pour un choix DÉLIBÉRÉ via le sélecteur.
            "choice": ({"pack": choice.get("pack"), "rel": choice.get("rel"),
                        "missing": not Path(choice["path"]).is_file()}
                       if choice else None),
        })
    return out


def image_dimensions(path: Path) -> tuple[int, int] | None:
    """(largeur, hauteur) si le fichier est une image lisible, sinon None (jamais d'exception :
    l'UI affiche une vignette, une image illisible ne doit pas casser la page)."""
    try:
        _, w, h = image_info(Path(path))
        return w, h
    except AssetError:
        return None
