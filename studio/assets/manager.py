"""Gestionnaire d'assets cosmétiques : hot-swap SÛRS avec backups et manifeste.

Principes non négociables :
  - COSMÉTIQUE UNIQUEMENT : images (cartes, tapis, dos, fonds) et localisation texte.
    Jamais de code, jamais de .assets/.dll — whitelist stricte de cibles.
  - RÉVERSIBLE À 100 % : l'original PRISTINE de chaque fichier touché est sauvegardé une
    seule fois (avant le premier swap) dans le dossier d'état du studio, avec un manifeste
    (hash original, pack appliqué, date). `restore` remet tout à l'identique.
  - ATOMIQUE : écriture via fichier temporaire + os.replace (jamais de fichier à moitié
    écrit si le sim démarre pendant un swap).
  - SANS ÉLÉVATION : si la cible n'est pas écrivable (droits /Applications), on explique —
    on ne sudo jamais, on ne chmod jamais.
  - VALIDÉ : chaque image est vérifiée (magic bytes PNG/JPEG + dimensions plausibles)
    avant d'écraser quoi que ce soit ; les symlinks sont refusés des deux côtés.

Réalités terrain (macOS, sim 1.41b — voir gamepaths.py) :
  - Remplacer un fichier du bundle .app invalide sa signature : le sim déjà autorisé se
    relance, mais `restore_all` est là si Gatekeeper proteste après une mise à jour d'OS.
  - Une mise à jour du sim écrase les swaps : les packs restent stockés côté studio,
    `apply_pack` se rejoue en une commande.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..gamepaths import GameInstall

# Callback de progression optionnel : on_progress(phase, done, total). Même contrat que
# packlib.OnProgress (types dupliqués volontairement pour ne pas coupler les deux modules).
OnProgress = Callable[[str, int, int], None]


def _noop_progress(phase: str, done: int, total: int) -> None:
    pass

DEFAULT_STATE_DIR = Path.home() / ".optcgsim-studio"

# Dimensions attendues (tolérance large : les proxies HD sont plus grands, même ratio).
CARD_RATIO = 480 / 671          # ≈ 0.715 (mesuré sur les assets réels)
CARD_RATIO_TOL = 0.06


class AssetError(Exception):
    pass


# --------------------------------------------------------------------------- validation image
def image_info(path: Path) -> tuple[str, int, int]:
    """(format 'png'|'jpeg', largeur, hauteur) — sans dépendance externe.

    Refuse tout ce qui n'est pas un PNG/JPEG valide : on ne copie JAMAIS un fichier
    non identifié dans le dossier du jeu.
    """
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "png", w, h
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):  # SOFn
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", w, h
            seg_len = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg_len
        raise AssetError(f"JPEG sans en-tête de dimensions : {path}")
    raise AssetError(f"Format d'image non reconnu (ni PNG ni JPEG) : {path}")


def _check_card_image(path: Path) -> None:
    fmt, w, h = image_info(path)
    if w < 200 or h < 280:
        raise AssetError(f"Image trop petite pour une carte ({w}×{h}) : {path}")
    ratio = w / h
    if abs(ratio - CARD_RATIO) > CARD_RATIO_TOL:
        raise AssetError(
            f"Ratio {ratio:.3f} hors gabarit carte (~{CARD_RATIO:.3f}) : {path}")


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- manager
@dataclass
class SwapEntry:
    target: str          # chemin absolu du fichier du jeu
    backup: str          # copie pristine côté studio
    original_sha1: str
    applied_sha1: str
    source: str          # fichier/pack d'origine du swap
    applied_at: float


class AssetManager:
    def __init__(self, install: GameInstall, state_dir: Path = DEFAULT_STATE_DIR):
        self.install = install
        self.state_dir = Path(state_dir)
        self.backup_dir = self.state_dir / "backups"
        self.manifest_path = self.state_dir / "manifest.json"
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._manifest: dict[str, dict] = {}
        if self.manifest_path.exists():
            self._manifest = json.loads(self.manifest_path.read_text())

    # ------------------------------------------------------------ garde-fous
    def _allowed_roots(self) -> list[Path]:
        return [self.install.streaming_assets.resolve(),
                self.install.persistent.resolve()]

    def _guard_target(self, target: Path) -> Path:
        t = target.resolve()
        if target.is_symlink() or t.is_symlink():
            raise AssetError(f"Symlink refusé : {target}")
        if not any(str(t).startswith(str(root) + os.sep) or t == root
                   for root in self._allowed_roots()):
            raise AssetError(f"Cible hors des zones d'assets autorisées : {t}")
        if t.suffix.lower() not in (".png", ".jpg", ".jpeg", ".txt"):
            raise AssetError(f"Extension non autorisée (cosmétique uniquement) : {t}")
        return t

    @staticmethod
    def _guard_source(source: Path) -> Path:
        s = Path(source)
        if s.is_symlink():
            raise AssetError(f"Symlink refusé : {s}")
        if not s.is_file():
            raise AssetError(f"Fichier source introuvable : {s}")
        return s

    # ------------------------------------------------------------ backup + swap atomique
    def _backup_once(self, target: Path) -> Path:
        """Sauvegarde PRISTINE : uniquement au premier swap de ce fichier."""
        key = str(target)
        if key in self._manifest:
            return Path(self._manifest[key]["backup"])
        bk = (self.backup_dir / hashlib.sha1(key.encode()).hexdigest()
              ).with_suffix(target.suffix)
        # Write-once sur DISQUE, pas seulement dans le manifeste : si `manifest.json` est perdu
        # ou corrompu (crash, Ctrl-C dans `_save_manifest`, nettoyage), le swap suivant
        # sauvegarderait le fichier DÉJÀ MODIFIÉ comme s'il était l'original — et `restore_all`
        # restaurerait alors le thème précédent en croyant restaurer le jeu d'origine.
        if bk.exists():
            return bk
        shutil.copy2(target, bk)
        return bk

    def _swap(self, target: Path, source: Path, origin: str) -> None:
        target = self._guard_target(target)
        source = self._guard_source(source)
        if not target.exists():
            raise AssetError(f"La cible n'existe pas dans le jeu : {target}")
        if not os.access(target.parent, os.W_OK):
            raise AssetError(
                f"Dossier non écrivable : {target.parent}\n"
                f"  (macOS : vérifie les droits sur l'app — le studio n'élève JAMAIS "
                f"ses privilèges)")
        backup = self._backup_once(target)
        original_sha1 = (self._manifest.get(str(target), {}).get("original_sha1")
                         or _sha1(target))
        # écriture atomique : tmp dans le MÊME dossier puis rename
        tmp = target.with_name(f".{target.name}.studio-tmp")
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
        self._manifest[str(target)] = SwapEntry(
            target=str(target), backup=str(backup), original_sha1=original_sha1,
            applied_sha1=_sha1(target), source=origin,
            applied_at=time.time()).__dict__
        self._save_manifest()

    def _save_manifest(self) -> None:
        tmp = self.manifest_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._manifest, indent=1))
        os.replace(tmp, self.manifest_path)

    # ------------------------------------------------------------ opérations publiques
    def apply_card(self, card_id: str, image: Path, origin: str | None = None) -> list[Path]:
        """Remplace TOUTES les occurrences d'une carte (base .png, miniature, caches .jpg).

        Sans conversion de format : seules les cibles du même format que la source sont
        remplacées (un PNG source remplace le .png de base ; un JPEG source remplace la
        miniature et les caches). Fournir les deux formats dans un pack couvre tout.
        """
        image = self._guard_source(image)
        _check_card_image(image)
        fmt, _, _ = image_info(image)
        targets = self.install.find_card_files(card_id)
        if not targets:
            raise AssetError(f"Carte inconnue de cette installation : {card_id}")
        done = []
        for t in targets:
            t_fmt = "png" if t.suffix.lower() == ".png" else "jpeg"
            if t_fmt != fmt:
                continue
            self._swap(t, image, origin or str(image))
            done.append(t)
        if not done:
            raise AssetError(
                f"Aucune cible {fmt} pour {card_id} (cibles : "
                f"{[t.name for t in targets]}) — fournir l'image dans le bon format")
        return done

    def apply_playmat(self, name: str, image: Path, origin: str | None = None) -> Path:
        image = self._guard_source(image)
        fmt, _, _ = image_info(image)
        target = self.install.playmats_dir / f"{name}.png"
        if fmt != "png":
            raise AssetError(f"Les tapis sont des PNG ({target.name}) : convertir d'abord")
        self._swap(target, image, origin or str(image))
        return target

    def apply_cardback(self, image: Path, don: bool = False,
                       origin: str | None = None) -> Path:
        image = self._guard_source(image)
        fmt, _, _ = image_info(image)
        if fmt != "png":
            raise AssetError("Les dos de cartes sont des PNG : convertir d'abord")
        name = "CardBackDon.png" if don else "CardBackRegular.png"
        target = self.install.cardbacks_dir / name
        self._swap(target, image, origin or str(image))
        return target

    def apply_background(self, image: Path, deck_editor: bool = False,
                         origin: str | None = None) -> Path:
        image = self._guard_source(image)
        fmt, _, _ = image_info(image)
        if fmt != "jpeg":
            raise AssetError("Les fonds sont des JPEG : convertir d'abord")
        name = "deckeditbackground.jpg" if deck_editor else "background.jpg"
        target = self.install.streaming_assets / name
        self._swap(target, image, origin or str(image))
        return target

    def apply_translation(self, overrides: Path, origin: str | None = None) -> Path:
        """FUSIONNE un fichier de traduction (Clé=Valeur) dans TRANSLATION.txt.

        Fusion et non remplacement : les clés inconnues du fichier utilisateur sont
        ignorées ; les clés absentes de l'override gardent le texte officiel. On ne
        casse jamais une clé attendue par le jeu.
        """
        overrides = self._guard_source(overrides)
        target = self.install.translation_file
        base = target.read_text(errors="ignore").splitlines()
        keys = {}
        for line in overrides.read_text(errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                keys[k.strip()] = v
        known = {ln.split("=", 1)[0] for ln in base if "=" in ln}
        merged = [f"{ln.split('=', 1)[0]}={keys[ln.split('=', 1)[0]]}"
                  if "=" in ln and ln.split("=", 1)[0] in keys else ln
                  for ln in base]
        unknown = set(keys) - known
        # écrit la fusion dans un tmp studio, puis passe par le chemin swap standard
        tmp = self.state_dir / "translation.merged.txt"
        tmp.write_text("\n".join(merged) + "\n")
        self._swap(target, tmp, origin or str(overrides))
        tmp.unlink(missing_ok=True)
        if unknown:
            print(f"⚠ {len(unknown)} clés inconnues ignorées (ex. {sorted(unknown)[:3]})")
        return target

    # ------------------------------------------------------------ miroir StreamingAssets (P0)
    # Racines connues d'un pack « miroir » (layout du site Themer & co) : les noms de premier
    # niveau qui, présents, signent un pack calqué sur StreamingAssets.
    _MIRROR_ROOTS = ("Cards", "Playmats", "CardBacks", "OPBounty")
    _MIRROR_FILES = ("background.jpg", "deckeditbackground.jpg")

    def _find_mirror_root(self, pack_dir: Path) -> Path:
        """Trouve la racine réelle du miroir : le dossier qui contient les entrées
        StreamingAssets connues, même s'il est enveloppé dans 1-2 dossiers (cas d'un zip
        décompressé avec un dossier englobant). Renvoie `pack_dir` si rien de plus profond."""
        def looks_like_mirror(d: Path) -> bool:
            names = {p.name for p in d.iterdir()} if d.is_dir() else set()
            return bool(set(self._MIRROR_ROOTS) & names) or bool(
                set(self._MIRROR_FILES) & names)

        if looks_like_mirror(pack_dir):
            return pack_dir
        # descente prudente : un seul niveau d'emballage, sinon on abandonne (pack_dir tel quel)
        subdirs = [p for p in pack_dir.iterdir() if p.is_dir()] if pack_dir.is_dir() else []
        for d in subdirs:
            if looks_like_mirror(d):
                return d
        return pack_dir

    @staticmethod
    def mirror_category(rel: str) -> str:
        """Catégorie d'un chemin miroir (pour le filtre `--only`)."""
        top = rel.replace("\\", "/").split("/")[0]
        if top == "Cards":
            return "cards"
        if top == "Playmats":
            return "playmats"
        if top == "CardBacks":
            return "cardbacks"
        if rel in ("background.jpg", "deckeditbackground.jpg"):
            return "backgrounds"
        return "other"

    def apply_mirror(self, pack_dir: Path, origin: str | None = None,
                     dry_run: bool = False, only: set[str] | None = None,
                     on_progress: OnProgress = _noop_progress) -> dict:
        """Applique un pack calqué sur StreamingAssets (modèle du site Themer & assimilés).

        Règle unique : pour chaque image du pack, si le MÊME chemin relatif existe déjà dans
        StreamingAssets et partage le même format, on le remplace (via `_swap` : backup +
        atomique + manifeste). Sinon on IGNORE en le rapportant — JAMAIS de création de
        fichier inconnu du jeu. Un pack ne peut donc que re-skinner de l'existant, et
        `restore_all()` défait tout.

        `dry_run=True` : analyse seulement — rien n'est écrit, `applied` liste ce qui LE
        SERAIT (idéal pour prévisualiser avant d'appliquer).

        Les .txt (traduction) sont volontairement EXCLUS : ils doivent passer par
        `apply_translation` (fusion préservant les clés officielles), pas par un écrasement.

        Renvoie un rapport {root, applied: [rel], ignored: [{path, reason}], skipped_txt}.
        """
        pack_dir = Path(pack_dir)
        root = self._find_mirror_root(pack_dir)
        origin = origin or f"mirror:{pack_dir.name}"
        sa = self.install.streaming_assets.resolve()
        report: dict = {"root": str(root), "applied": [], "ignored": [],
                        "skipped_txt": [], "filtered": [], "collisions": []}

        candidates = sorted(p for p in root.rglob("*") if p.is_file())
        for i, src in enumerate(candidates, 1):
            on_progress("apply", i, len(candidates))
            if src.is_symlink():
                report["ignored"].append({"path": src.name, "reason": "symlink refusé"})
                continue
            rel = src.relative_to(root)
            ext = src.suffix.lower()
            if ext == ".txt":
                report["skipped_txt"].append(str(rel))
                continue
            if ext not in (".png", ".jpg", ".jpeg"):
                continue        # non-image (manifest.json, README, .DS_Store…) : ignoré en silence
            if only is not None and self.mirror_category(str(rel)) not in only:
                report["filtered"].append(str(rel))
                continue
            target = (sa / rel)
            if not target.exists():
                report["ignored"].append(
                    {"path": str(rel), "reason": "aucune cible correspondante dans le jeu"})
                continue
            # collision : cette cible est déjà tenue par un AUTRE pack (dernier appliqué gagne ;
            # le backup reste l'ORIGINAL, donc restore ramène toujours au jeu d'origine).
            prev = self._manifest.get(str(target.resolve()))
            if prev and prev.get("source") not in (None, origin):
                report["collisions"].append({"path": str(rel), "previous": prev["source"]})
            # format : le miroir doit respecter le format de la cible existante
            t_fmt = "png" if target.suffix.lower() == ".png" else "jpeg"
            try:
                s_fmt, _, _ = image_info(src)
            except AssetError as e:
                report["ignored"].append({"path": str(rel), "reason": str(e)})
                continue
            if s_fmt != t_fmt:
                report["ignored"].append(
                    {"path": str(rel), "reason": f"format {s_fmt} ≠ cible {t_fmt}"})
                continue
            if dry_run:
                report["applied"].append(str(rel))         # ce qui SERAIT remplacé
                continue
            try:
                self._swap(target, src, origin)
                report["applied"].append(str(rel))
            except AssetError as e:
                report["ignored"].append({"path": str(rel), "reason": str(e)})
        return report

    def apply_pack(self, pack_dir: Path) -> dict[str, int]:
        """Applique un pack d'assets (structure = celle que le frontend produit en D&D) :

            pack/
              cards/<ID>.png|.jpg       (les deux formats si dispo)
              playmats/<Nom>.png
              cardback.png / cardback_don.png
              background.jpg / deckeditbackground.jpg
              translation.txt
        """
        pack_dir = Path(pack_dir)
        origin = f"pack:{pack_dir.name}"
        counts = {"cards": 0, "playmats": 0, "cardbacks": 0,
                  "backgrounds": 0, "translation": 0}
        cards = pack_dir / "cards"
        if cards.is_dir():
            for img in sorted(cards.iterdir()):
                if img.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    cid = img.stem.removesuffix("_small")
                    try:
                        self.apply_card(cid, img, origin)
                        counts["cards"] += 1
                    except AssetError as e:
                        print(f"  skip {img.name}: {e}")
        mats = pack_dir / "playmats"
        if mats.is_dir():
            for img in sorted(mats.glob("*.png")):
                self.apply_playmat(img.stem, img, origin)
                counts["playmats"] += 1
        for fname, kw in (("cardback.png", {}), ("cardback_don.png", {"don": True})):
            if (pack_dir / fname).exists():
                self.apply_cardback(pack_dir / fname, origin=origin, **kw)
                counts["cardbacks"] += 1
        for fname, kw in (("background.jpg", {}),
                          ("deckeditbackground.jpg", {"deck_editor": True})):
            if (pack_dir / fname).exists():
                self.apply_background(pack_dir / fname, origin=origin, **kw)
                counts["backgrounds"] += 1
        if (pack_dir / "translation.txt").exists():
            self.apply_translation(pack_dir / "translation.txt", origin)
            counts["translation"] += 1
        return counts

    # ------------------------------------------------------------ statut / restauration
    def status(self) -> list[dict]:
        """État de chaque swap : intact, écrasé par une màj du sim, ou restauré à la main."""
        out = []
        for key, e in sorted(self._manifest.items()):
            t = Path(key)
            state = ("missing" if not t.exists()
                     else "active" if _sha1(t) == e["applied_sha1"]
                     else "original" if _sha1(t) == e["original_sha1"]
                     else "overwritten")   # màj du sim ou modif externe
            out.append({**e, "state": state})
        return out

    def restore(self, target: Path) -> None:
        key = str(Path(target).resolve())
        e = self._manifest.get(key) or self._manifest.get(str(target))
        if e is None:
            raise AssetError(f"Aucun backup connu pour : {target}")
        t = self._guard_target(Path(e["target"]))
        tmp = t.with_name(f".{t.name}.studio-tmp")
        shutil.copyfile(e["backup"], tmp)
        os.replace(tmp, t)
        del self._manifest[e["target"]]
        self._save_manifest()

    def restore_all(self) -> dict:
        """Restaure TOUT ce qui peut l'être et renvoie `{"restored": n, "failed": [...]}`.

        `restore-all` est la commande de secours du projet : un échec sur une cible (dossier
        devenu non écrivable après une màj du sim, cible refusée par `_guard_target`) ne doit
        jamais laisser les swaps SUIVANTS en place. Même politique que `deckpack.resolve()` :
        un échec n'interrompt pas les autres, il ressort dans le rapport.
        """
        n, failed = 0, []
        for key in list(self._manifest):
            entry = self._manifest[key]
            if Path(entry["target"]).exists():
                try:
                    self.restore(Path(entry["target"]))
                    n += 1
                except (AssetError, OSError) as e:
                    failed.append({"target": entry["target"], "reason": str(e)})
            else:   # cible disparue (màj du sim) : le backup n'a plus d'objet
                del self._manifest[key]
                self._save_manifest()
        return {"restored": n, "failed": failed}

    def restore_source(self, origin: str) -> int:
        """Restaure uniquement les cibles posées par un pack donné (`origin`).

        Ne restaure PAS une cible re-tenue depuis par un autre pack (collision : le dernier
        appliqué reste ; retirer le pack sous-jacent ne doit pas défaire le pack visible)."""
        n = 0
        for key in list(self._manifest):
            entry = self._manifest[key]
            if entry.get("source") != origin:
                continue
            if Path(entry["target"]).exists():
                self.restore(Path(entry["target"]))
                n += 1
            else:
                del self._manifest[key]
                self._save_manifest()
        return n

    def inventory(self) -> dict:
        """Ce que cette installation expose de remplaçable (pour l'UI)."""
        inst = self.install
        sets = sorted(p.name for p in inst.cards_dir.iterdir()
                      if p.is_dir()) if inst.cards_dir.exists() else []
        return {
            "os": inst.os_name, "verified": inst.verified,
            "streaming_assets": str(inst.streaming_assets),
            "card_sets": sets,
            "playmats": sorted(p.stem for p in inst.playmats_dir.glob("*.png"))
                        if inst.playmats_dir.exists() else [],
            "cardbacks": ["regular", "don"],
            "backgrounds": ["main", "deck_editor"],
            "translation": inst.translation_file.exists(),
            "version_caches": [p.name for p in inst.version_dirs()],
            "active_swaps": len(self._manifest),
        }
