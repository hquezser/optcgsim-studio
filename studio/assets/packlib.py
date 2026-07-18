"""packlib — normalise n'importe quelle source d'assets en PACK CANONIQUE (layout miroir
de StreamingAssets), prêt pour `AssetManager.apply_mirror` + `apply_translation`.

Sources réelles ciblées (inspectées) et layout produit :

    optcgsimthemer.com (zip)   : DÉJÀ un miroir StreamingAssets       -> copié tel quel
    Dropbox « Alt Cards Jon »  : Cards/<SET>/<ID>.png (+_small.jpg)   -> miroir (sous Cards/)
    GitHub OPTCGSim_FR         : TRANSLATION.txt + <SET>/<ID>_OVERRIDE.png
                                                            -> TRANSLATION.txt + Cards/<SET>/<ID>.png

Principe : classification PAR FICHIER (pas de layout global), dans cet ordre —
  1. le fichier est sous une racine miroir connue (Cards/ Playmats/ CardBacks/ OPBounty/)
     -> on préserve son chemin relatif à partir de cette racine ;
  2. sinon son nom donne un id de carte (gabarit CARD_ID, suffixes parasites retirés)
     -> Cards/<SET>/<ID>[_small].<ext> ;
  3. sinon fichier spécial par nom : .txt Clé=Valeur -> TRANSLATION.txt ; « cardback » ->
     CardBacks/… ; nom de playmat connu -> Playmats/… ; « background »/« deckedit » -> fond ;
  4. sinon NON CLASSÉ (rapporté avec raison — jamais de silence).

La normalisation ne fait qu'organiser des fichiers dans un dossier de bibliothèque ; elle
n'écrit RIEN dans le jeu (c'est le rôle du manager, seul détenteur des garde-fous d'écriture).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..gamepaths import GameInstall
from ..nettls import CERT_FIX_HINT, is_cert_error, ssl_context

# Callback de progression optionnel : on_progress(phase, done, total). `total == 0` = inconnu.
OnProgress = Callable[[str, int, int], None]


def _noop_progress(phase: str, done: int, total: int) -> None:
    pass

# Gabarit d'id partagé avec le reste de l'écosystème.
CARD_ID = r"(?:P-[A-Z0-9]+|[A-Z]{2,4}\d{2}-\d{3})"
_CARD_STEM = re.compile(rf"^({CARD_ID})$")

IMAGE_EXT = (".png", ".jpg", ".jpeg")
MIRROR_ROOTS = ("Cards", "Playmats", "CardBacks", "OPBounty")

DEFAULT_LIB = Path.home() / ".optcgsim-studio" / "packs"

# Suffixes de nom PARASITES (conventions de distribution) à retirer pour retrouver l'id du
# sim. « _small » est un VRAI suffixe du jeu (miniature) -> jamais retiré ici.
_STRIP_SUFFIXES = ("_OVERRIDE", "_override", "_alt", "_ALT", "_v2", "_V2", "_full", "_fullart")


class PackError(Exception):
    pass


@dataclass
class PackReport:
    name: str
    source: str
    applied_layout: str = "mixed"                      # info d'affichage
    cards: list[str] = field(default_factory=list)     # ids reconnus (dédupliqués)
    playmats: list[str] = field(default_factory=list)
    cardbacks: list[str] = field(default_factory=list)
    backgrounds: list[str] = field(default_factory=list)
    translation: bool = False
    unclassified: list[dict] = field(default_factory=list)   # {path, reason}
    variants: list[dict] = field(default_factory=list)       # {target, kept, dropped}
    filtered: list[str] = field(default_factory=list)   # exclus par choix (only_cards/categories)
    present_in_install: int = 0                        # combien de cibles existent déjà
    total_files: int = 0
    files: dict = field(default_factory=dict)          # rel -> sha1 (delta d'update)

    def summary(self) -> str:
        base = (f"{len(self.cards)} cartes, {len(self.playmats)} playmats, "
                f"{len(self.cardbacks)} dos, {len(self.backgrounds)} fonds, "
                f"traduction={'oui' if self.translation else 'non'} ; "
                f"{self.present_in_install} cibles présentes dans le jeu ; "
                f"{len(self.unclassified)} non classés")
        if self.filtered:
            base += f" ; {len(self.filtered)} hors périmètre (filtrés)"
        return base


# --------------------------------------------------------------------------- filtre sélectif
def _card_id_from_stem(stem: str) -> str | None:
    """Id de carte depuis un nom de fichier (sans ext), suffixes parasites/`_small` retirés."""
    core = stem[:-6] if stem.endswith("_small") else stem
    core = _strip_parasite(core)
    m = _CARD_STEM.match(core)
    return m.group(1) if m else None


def classify_rel(rel: str) -> tuple[str, str | None]:
    """Catégorie + id de carte d'un chemin SOURCE, par nom (sans lire le fichier).

    Utilisé identiquement pour filtrer AVANT téléchargement (chemins distants GitHub) et à la
    normalisation (fichiers locaux). Catégories : cards / playmats / cardbacks / backgrounds
    / translation / other. `card_id` non-None uniquement pour les cartes.
    """
    parts = rel.replace("\\", "/").split("/")
    name = parts[-1]
    dot = name.rfind(".")
    stem, ext = (name[:dot], name[dot:].lower()) if dot >= 0 else (name, "")
    if ext == ".txt":
        return ("translation", None)
    if ext not in IMAGE_EXT:
        return ("other", None)
    for i, p in enumerate(parts[:-1]):
        if p in MIRROR_ROOTS:
            if p == "Cards":
                # Cards/Don/Don.png -> catégorie « don » (filtrable à part) ; sinon carte.
                if i + 1 < len(parts) - 1 and parts[i + 1] == "Don":
                    return ("don", None)
                return ("cards", _card_id_from_stem(stem))
            return ({"Playmats": "playmats", "CardBacks": "cardbacks",
                     "OPBounty": "other"}[p], None)
    if name in ("background.jpg", "deckeditbackground.jpg"):
        return ("backgrounds", None)
    cid = _card_id_from_stem(stem)
    if cid:
        return ("cards", cid)
    low = _strip_parasite(stem).lower()
    if "cardback" in low:
        return ("cardbacks", None)
    if "background" in low or "deckedit" in low:
        return ("backgrounds", None)
    return ("other", None)


def keep_rel(rel: str, only_categories: set[str] | None,
             only_cards: set[str] | None) -> bool:
    """Le fichier passe-t-il le filtre sélectif ? (les deux filtres composent en ET)."""
    cat, cid = classify_rel(rel)
    if only_categories is not None and cat not in only_categories:
        return False
    if only_cards is not None and cat == "cards" and (cid is None or cid not in only_cards):
        return False
    return True


# --------------------------------------------------------------------------- ingestion
def _safe_extract(zf: zipfile.ZipFile, dest: Path,
                  on_progress: OnProgress = _noop_progress) -> None:
    """Extraction protégée contre le zip-slip (aucun membre hors de `dest`), avec progression.

    Les exports Dropbox de dossier incluent une entrée `/` en tête (marqueur du dossier
    racine lui-même, sans contenu) : `member.filename` vaut alors littéralement `/`.
    `pathlib` traite un opérande ABSOLU dans `dest / nom` en IGNORANT `dest` (résultat = `/`,
    la racine du système de fichiers) — un faux positif de zip-slip sur une archive
    parfaitement légitime. On reproduit donc la même normalisation que
    `zipfile.ZipFile.extractall()` applique déjà en interne (retrait des séparateurs de tête)
    avant de vérifier le confinement ; une entrée qui devient vide après ce retrait est un
    simple marqueur de dossier racine, sans contenu à valider.

    La vérification et l'extraction sont fusionnées en une seule passe membre par membre
    (plutôt que vérifier puis appeler `extractall`) : la progression (fichier N/total) en
    découle sans coût supplémentaire.
    """
    dest = dest.resolve()
    members = zf.infolist()
    total = len(members)
    for i, member in enumerate(members, 1):
        name = member.filename.lstrip("/\\")
        if name:      # sinon : marqueur de dossier racine (entrée '/' des exports Dropbox)
            target = (dest / name).resolve()
            if not (target == dest or str(target).startswith(str(dest) + "/")):
                raise PackError(f"Entrée d'archive hors dossier (zip-slip) : {member.filename}")
        zf.extract(member, dest)
        on_progress("extract", i, total)


def _download(url: str, dest_zip: Path, timeout: float = 60.0,
             on_progress: OnProgress = _noop_progress) -> None:
    """Télécharge en streaming (1 Mo/bloc). Les dossiers communautaires complets peuvent
    peser plusieurs centaines de Mo (ex. « Alt Cards Jon » ≈ 614 Mo) : `on_progress` permet
    à l'appelant (CLI, API) d'afficher une progression pour que ça ne ressemble pas à un
    blocage — packlib ne fait aucune hypothèse sur la présentation (terminal, JSON de job…)."""
    req = urllib.request.Request(url, headers={"User-Agent": "optcgsim-studio/0.1"})
    try:
        with urllib.request.urlopen(  # noqa: S310 (schéma vérifié)
                req, timeout=timeout, context=ssl_context()) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with dest_zip.open("wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    on_progress("download", done, total)
    except urllib.error.URLError as e:
        # urlopen() enveloppe TOUJOURS un échec de handshake SSL dans un URLError (le
        # ssl.SSLCertVerificationError brut ne remonte jamais tel quel) : is_cert_error
        # inspecte `.reason` pour le détecter ; toute autre URLError (DNS, connexion
        # refusée…) est relayée telle quelle, sans message d'aide trompeur.
        if is_cert_error(e):
            raise PackError(CERT_FIX_HINT) from e
        raise


def _resolve_url(url: str) -> str:
    """Transforme une URL de page en URL de zip téléchargeable."""
    u = url.rstrip("/")
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)(?:/tree/([^/]+))?$", u)
    if m:
        owner, repo, branch = m.group(1), m.group(2), m.group(3) or "main"
        return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
    if "dropbox.com" in u:
        return re.sub(r"([?&])dl=0", r"\1dl=1", u) if "dl=" in u else u + (
            "&dl=1" if "?" in u else "?dl=1")
    return url


def ingest(source: str | Path, work_dir: Path,
          on_progress: OnProgress = _noop_progress) -> Path:
    """Résout une source (dossier, zip local, URL) en un dossier local extrait.

    Le réseau n'est touché que pour les URL http(s) ; dossiers et zips locaux sont hors-ligne.
    Renvoie le dossier racine des fichiers extraits.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    s = str(source)
    if s.lower().startswith(("http://", "https://")):
        zpath = work_dir / "download.zip"
        _download(_resolve_url(s), zpath, on_progress=on_progress)
        out = work_dir / "extracted"
        with zipfile.ZipFile(zpath) as zf:
            _safe_extract(zf, out, on_progress=on_progress)
        return out
    p = Path(source)
    if p.is_dir():
        return p
    if p.suffix.lower() == ".zip" and p.is_file():
        out = work_dir / "extracted"
        with zipfile.ZipFile(p) as zf:
            _safe_extract(zf, out, on_progress=on_progress)
        return out
    raise PackError(f"Source non exploitable (ni dossier, ni zip, ni URL) : {source}")


# --------------------------------------------------------------------------- classification
def _mirror_rel(path: Path, root: Path) -> Path | None:
    """Chemin relatif à partir d'une racine miroir présente dans les ancêtres, sinon None."""
    parts = path.relative_to(root).parts
    for i, name in enumerate(parts):
        if name in MIRROR_ROOTS:
            return Path(*parts[i:])
    return None


def _strip_parasite(stem: str) -> str:
    for suf in _STRIP_SUFFIXES:
        if stem.endswith(suf):
            return stem[: -len(suf)]
    return stem


def _card_target(stem: str, ext: str) -> Path | None:
    """Cible canonique Cards/<SET>/<ID>[_small].<ext> depuis un nom de fichier, ou None."""
    small = stem.endswith("_small")
    core = stem[:-6] if small else stem
    core = _strip_parasite(core)
    m = _CARD_STEM.match(core)
    if not m:
        return None
    cid = m.group(1)
    set_code = cid.split("-")[0] if "-" in cid else cid
    name = f"{cid}_small{ext}" if small else f"{cid}{ext}"
    return Path("Cards") / set_code / name


def _is_translation(path: Path) -> bool:
    if path.suffix.lower() != ".txt":
        return False
    try:
        head = path.read_text(errors="ignore")[:4000]
    except OSError:
        return False
    kv = [ln for ln in head.splitlines() if "=" in ln and not ln.lstrip().startswith("#")]
    return len(kv) >= 5          # au moins quelques paires Clé=Valeur


def _special_target(path: Path, install: GameInstall) -> Path | None:
    """Cible pour dos/tapis/fond d'après le nom de fichier (hors cartes)."""
    stem = _strip_parasite(path.stem)
    low = stem.lower()
    ext = path.suffix.lower()
    if "cardback" in low or "card_back" in low:
        return Path("CardBacks") / ("CardBackDon.png" if "don" in low else "CardBackRegular.png")
    if "deckedit" in low:
        return Path("deckeditbackground.jpg")
    if low in ("background", "mainbackground", "menubackground"):
        return Path("background.jpg")
    # nom de playmat connu de l'install (ex. « Blue », « RedBlack »)
    known = {p.stem.lower() for p in install.playmats_dir.glob("*.png")} \
        if install.playmats_dir.exists() else set()
    if ext == ".png" and low in known:
        return Path("Playmats") / f"{stem}.png"
    return None


# --------------------------------------------------------------------------- normalisation
def normalize(src_dir: Path, install: GameInstall, name: str,
              source: str, lib_dir: Path = DEFAULT_LIB,
              on_progress: OnProgress = _noop_progress,
              only_categories: set[str] | None = None,
              only_cards: set[str] | None = None) -> tuple[Path, PackReport]:
    """Écrit un pack canonique (miroir) dans `lib_dir/<name>/` et renvoie (chemin, rapport).

    Ne touche jamais au jeu. Idempotent : réécrit proprement le dossier du pack.

    `only_categories`/`only_cards` : import SÉLECTIF (P7) — un fichier hors périmètre n'est
    PAS copié dans la bibliothèque (économie disque réelle) et ressort dans `rep.filtered`,
    distinct de `unclassified` (un exclu volontaire n'est pas une erreur de reconnaissance).
    """
    src_dir = Path(src_dir)
    pack_dir = Path(lib_dir) / name
    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True)
    rep = PackReport(name=name, source=source)
    sa = install.streaming_assets

    # cible canonique -> fichier source retenu (pour gérer les variantes/collisions)
    chosen: dict[str, Path] = {}

    def place(rel_target: Path, src: Path) -> None:
        key = str(rel_target)
        if key in chosen:
            rep.variants.append({"target": key, "kept": str(chosen[key].name),
                                 "dropped": src.name})
            return              # 1er vu gardé (déterministe) ; variante rapportée
        chosen[key] = src

    translation_src: Path | None = None
    all_files = sorted(p for p in src_dir.rglob("*") if p.is_file())
    for i, f in enumerate(all_files, 1):
        on_progress("classify", i, len(all_files))
        if f.is_symlink():
            rep.unclassified.append({"path": f.name, "reason": "symlink refusé"})
            continue
        rel_str = str(f.relative_to(src_dir))
        # filtre sélectif (P7) : exclu par CHOIX -> rapporté à part, jamais copié.
        if (only_categories is not None or only_cards is not None) \
                and not keep_rel(rel_str, only_categories, only_cards):
            rep.filtered.append(rel_str)
            continue
        ext = f.suffix.lower()
        # 0. traduction
        if _is_translation(f):
            if translation_src is None:
                translation_src = f
                rep.translation = True
            else:
                rep.unclassified.append({"path": str(f.relative_to(src_dir)),
                                         "reason": "2e fichier de traduction ignoré"})
            continue
        if ext not in IMAGE_EXT:
            continue            # bruit (README, .DS_Store, .ps1…) : silencieux
        rep.total_files += 1
        # 1. sous une racine miroir ?
        mrel = _mirror_rel(f, src_dir)
        if mrel is not None:
            # normalise le nom de fichier (retire _OVERRIDE etc.) en gardant l'arbo
            target = mrel.parent / (_strip_parasite(mrel.stem) + mrel.suffix)
            place(target, f)
            continue
        # 2. id de carte dans le nom ?
        ct = _card_target(f.stem, ext)
        if ct is not None:
            place(ct, f)
            continue
        # 3. fichier spécial (dos/tapis/fond) ?
        st = _special_target(f, install)
        if st is not None:
            place(st, f)
            continue
        # 4. non classé
        rep.unclassified.append(
            {"path": str(f.relative_to(src_dir)),
             "reason": "ni chemin miroir, ni id de carte, ni asset spécial reconnu"})

    # matérialise le pack canonique + empreinte par fichier (pour le delta d'`update`)
    files: dict[str, str] = {}
    n_chosen = len(chosen)
    for i, (rel, src) in enumerate(chosen.items(), 1):
        on_progress("copy", i, n_chosen)
        dst = pack_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        files[rel] = hashlib.sha1(dst.read_bytes()).hexdigest()
        top = rel.split("/")[0]
        if top == "Cards":
            m = re.search(CARD_ID, rel)
            if m:
                rep.cards.append(m.group(0))
        elif top == "Playmats":
            rep.playmats.append(Path(rel).stem)
        elif top == "CardBacks":
            rep.cardbacks.append(Path(rel).stem)
        elif rel in ("background.jpg", "deckeditbackground.jpg"):
            rep.backgrounds.append(rel)
        if (sa / rel).exists():
            rep.present_in_install += 1
    rep.cards = sorted(set(rep.cards))

    if translation_src is not None:
        dst = pack_dir / "TRANSLATION.txt"
        shutil.copy2(translation_src, dst)
        files["TRANSLATION.txt"] = hashlib.sha1(dst.read_bytes()).hexdigest()
    rep.files = files

    (pack_dir / "manifest.json").write_text(json.dumps({
        "name": name, "source": source,
        "cards": rep.cards, "playmats": sorted(rep.playmats),
        "cardbacks": sorted(rep.cardbacks), "backgrounds": sorted(rep.backgrounds),
        "translation": rep.translation, "present_in_install": rep.present_in_install,
        "unclassified": rep.unclassified, "variants": rep.variants,
        "filtered": len(rep.filtered), "files": files,
    }, indent=1, ensure_ascii=False))
    return pack_dir, rep


def add_pack(source: str | Path, install: GameInstall, name: str | None = None,
             lib_dir: Path = DEFAULT_LIB, work_dir: Path | None = None,
             on_progress: OnProgress = _noop_progress,
             only_categories: set[str] | None = None,
             only_cards: set[str] | None = None,
             token: str | None = None) -> tuple[Path, PackReport]:
    """Bout-en-bout : ingère une source puis la normalise en pack de bibliothèque.

    `on_progress(phase, done, total)` reçoit les phases "download" (réseau), "extract"
    (zip), "classify" et "copy" (normalisation) — permet à l'appelant d'afficher une
    progression sur des opérations qui peuvent durer plusieurs minutes (dossiers
    communautaires de plusieurs centaines de Mo).

    Import SÉLECTIF (P7) : si `only_categories`/`only_cards` est fourni ET que la source
    supporte l'exploration distante (GitHub, cf. sourcefetch) → on ne TÉLÉCHARGE que les
    fichiers retenus (économie disque ET bande passante, 98 % mesuré). Sinon (Dropbox, zip,
    dossier local) → téléchargement complet puis filtrage à la normalisation (disque
    seulement). `token` : PAT GitHub pour un dépôt privé.
    """
    from . import sourcefetch  # import local : évite un cycle et le coût si non utilisé

    work = work_dir or (Path(lib_dir) / ".work")
    has_filter = only_categories is not None or only_cards is not None
    try:
        src = None
        # --- chemin fetch-sélectif (source explorable + filtre actif) ---
        if has_filter:
            remote = None
            try:
                remote = sourcefetch.list_remote_files(str(source), token=token)
            except sourcefetch.FetchError:
                remote = None       # exploration impossible -> repli téléchargement complet
            if remote is not None:
                kept = [rf.path for rf in remote
                        if keep_rel(rf.path, only_categories, only_cards)]
                src = sourcefetch.fetch_selected(str(source), kept, work / "selected",
                                                 token=token, on_progress=on_progress)
        # --- chemin complet (défaut / sources non explorables) ---
        if src is None:
            src = ingest(source, work, on_progress=on_progress)
        # racine « utile » : si un seul sous-dossier enveloppe tout (zip GitHub), descendre
        entries = [p for p in src.iterdir()] if src.is_dir() else []
        if len(entries) == 1 and entries[0].is_dir():
            src = entries[0]
        pack_name = name or re.sub(r"[^\w.-]", "_", Path(str(source)).stem) or "pack"
        # Le filtre est TOUJOURS repassé à normalize : sur le chemin fetch-sélectif il est
        # déjà satisfait (no-op), sur le chemin complet c'est lui qui économise le disque.
        return normalize(src, install, pack_name, str(source), lib_dir,
                         on_progress=on_progress, only_categories=only_categories,
                         only_cards=only_cards)
    finally:
        # Nettoyage systématique (succès COMME échec) : un ingest() qui échoue à mi-chemin ne
        # doit pas laisser de zip/dossier orphelin dans la bibliothèque.
        if work.exists() and work != Path(lib_dir):
            shutil.rmtree(work, ignore_errors=True)
