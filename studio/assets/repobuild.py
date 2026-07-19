"""Constructeur de dépôts d'images (extension P8).

À partir de sources hétérogènes — **GitHub** (dépôt/zip), **Dropbox** (dossier partagé en
zip), **Google Drive** (fichier/zip partagé) — produit une arborescence **git-ready par
famille**, prête à pousser sur des dépôts PRIVÉS distincts :

    cards-alt/       Leaders/Cards/<SET>/<ID>.png, Events/…, Don/Cards/Don/…  (arts alternatifs)
    translations/    Leaders/Cards/… + TRANSLATION.txt                       (cartes traduites)
    playmats/        Playmats/<nom>.png, background.jpg
    cardbacks/       CardBacks/<nom>.png                        (dos de cartes UNIQUEMENT)

Pourquoi plusieurs dépôts (décision P8) : les images sont lourdes ; un dépôt par famille
reste sous les limites GitHub et se met à jour indépendamment. Les cartes sont **sous-classées
par type** (Leaders/Characters/Events/Stages/Don/Unknown) via `cardmeta` (Don à part : c'est
un reskin d'asset carte, PAS un dos de carte — `CardBacks/` reste réservé aux vrais dos), de
sorte que :
  1. l'import granulaire P8 (`--only-type leader`) fonctionne directement sur le dépôt poussé ;
  2. si une famille dépasse la limite, on scinde en déplaçant un simple sous-dossier de type.

Le layout reste **compatible import** : chaque carte est sous un ancêtre `Cards/`, donc
`packlib.classify_rel` la reconnaît (le préfixe de type est ignoré à l'application, cf.
`_mirror_rel`). Ce module ne POUSSE rien : la publication reste une action de l'utilisateur
(on écrit les dossiers, un `git init` et un `.gitignore`, et on affiche la recette de push).

**Mise à jour à chaque sortie de set** (`update()` / `studio repos update`) : chaque appel de
`build()` enregistre ses sources dans `<out_dir>/.repos-build.json` (sibling des dépôts, donc
jamais poussé avec eux). `update()` rejoue ces configurations sans que l'utilisateur n'ait à
retaper ses liens, et calcule un **diff par dépôt** (ajoutés/modifiés/orphelins, par sha1
contre le `MANIFEST.json` précédent) pour savoir quoi committer. Rien n'est jamais supprimé
automatiquement : un fichier disparu de la source devient un « orphelin » signalé, pas effacé.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import cardmeta, packlib

# Sous-dossier de type de carte -> pour l'organisation ET la scission éventuelle.
CARD_TYPE_DIR = {"Leader": "Leaders", "Character": "Characters",
                 "Event": "Events", "Stage": "Stages"}
UNKNOWN_TYPE_DIR = "Unknown"
# DON!! n'a pas d'id de carte (cid=None dans classify_rel) donc pas de card_type() cardmeta,
# mais c'est bien une CARTE (un reskin d'asset carte) et non un dos de carte : son propre
# sous-dossier de type, dans la famille CARTES (cards-alt/translations), pas cardbacks.
DON_TYPE_DIR = "Don"
_CARD_TYPE_DIRS = set(CARD_TYPE_DIR.values()) | {UNKNOWN_TYPE_DIR, DON_TYPE_DIR}

# Familles à destination déterministe (indépendantes du tag `cards_as`) — UNIQUEMENT les
# vrais dos de cartes et le reste ; DON!! est routé avec les cartes (cf. route()).
_FIXED_FAMILY = {"cardbacks": "cardbacks", "playmats": "playmats",
                 "backgrounds": "playmats", "translation": "translations"}

GITHUB_SOFT_LIMIT = 900 * 1024 * 1024   # ~900 Mo : au-delà, on conseille de scinder
_LOG_NAME = ".repos-build.json"


@dataclass
class RepoStat:
    family: str
    files: int = 0
    bytes: int = 0
    by_type: dict = field(default_factory=dict)   # {TypeDir: n} pour les dépôts de cartes
    added: list = field(default_factory=list)      # rel paths NOUVEAUX depuis le build précédent
    changed: list = field(default_factory=list)    # rel paths dont le sha1 a changé
    orphans: list = field(default_factory=list)    # rel paths qui n'existent plus en source

    @property
    def oversize(self) -> bool:
        return self.bytes > GITHUB_SOFT_LIMIT


@dataclass
class RepoBuildReport:
    out_dir: Path
    repos: dict = field(default_factory=dict)        # family -> RepoStat
    sources: list = field(default_factory=list)
    unclassified: list = field(default_factory=list)  # {source, path}
    collisions: list = field(default_factory=list)    # {repo, rel}

    def summary(self) -> str:
        parts = [f"{s.files} fichiers/{s.bytes // (1024*1024)} Mo « {fam} »"
                 for fam, s in sorted(self.repos.items())]
        s = f"{len(self.sources)} source(s) → " + (", ".join(parts) or "aucun fichier classé")
        if self.unclassified:
            s += f" ; {len(self.unclassified)} non classé(s)"
        if self.collisions:
            s += f" ; {len(self.collisions)} collision(s)"
        return s


def _card_family(cards_as: str) -> str:
    """« alt »/« translated » -> nom de dépôt ; toute autre valeur est prise telle quelle."""
    return {"alt": "cards-alt", "translated": "translations",
            "translation": "translations"}.get(cards_as, cards_as)


def _canonical_card_name(cid: str, filename: str) -> str:
    """Nom de fichier canonique du sim : <ID>.<ext> (et <ID>_small.jpg), suffixes parasites
    retirés — pour un dépôt propre et directement applicable."""
    dot = filename.rfind(".")
    ext = filename[dot:].lower() if dot >= 0 else ""
    stem = filename[:dot] if dot >= 0 else filename
    small = "_small" if stem.endswith("_small") else ""
    return f"{cid}{small}{ext}"


def route(cat: str, cid: str | None, filename: str, cards_as: str,
          split_cards_by_type: bool) -> tuple[str | None, str | None]:
    """(catégorie, id, nom) -> (dépôt cible, chemin relatif dans le dépôt), ou (None, None)
    si le fichier n'est pas à retenir (catégorie « other »)."""
    if cat == "cards":
        fam = _card_family(cards_as)
        set_ = cid.split("-")[0] if cid else "UNKNOWN"
        name = _canonical_card_name(cid, filename) if cid else filename
        if split_cards_by_type:
            sub = CARD_TYPE_DIR.get(cardmeta.card_type(cid) if cid else None, UNKNOWN_TYPE_DIR)
            return fam, f"{sub}/Cards/{set_}/{name}"
        return fam, f"Cards/{set_}/{name}"
    if cat == "don":
        # une carte (reskin de la DON!!), pas un dos de carte -> famille CARTES.
        fam = _card_family(cards_as)
        if split_cards_by_type:
            return fam, f"{DON_TYPE_DIR}/Cards/Don/{filename}"
        return fam, f"Cards/Don/{filename}"
    if cat in _FIXED_FAMILY:
        prefix = {"cardbacks": "CardBacks/", "playmats": "Playmats/"}.get(cat, "")
        return _FIXED_FAMILY[cat], f"{prefix}{filename}"
    return None, None


def _by_type_from_written(files: dict) -> dict:
    """Reconstruit le compte par type à partir des chemins relatifs déjà routés (pas besoin
    de le suivre pendant la copie : le chemin encode déjà le type sous-dossier)."""
    counts: dict[str, int] = {}
    for rel in files:
        parts = rel.split("/")
        if len(parts) >= 2 and parts[0] in _CARD_TYPE_DIRS and parts[1] == "Cards":
            counts[parts[0]] = counts.get(parts[0], 0) + 1
        elif parts[0] == "Cards":   # split_cards_by_type=False : layout plat
            counts["Cards"] = counts.get("Cards", 0) + 1
    return counts


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def build(sources: list[str], out_dir: Path, *, cards_as: str = "alt",
          split_cards_by_type: bool = True, git_init: bool = True,
          work_dir: Path | None = None, ingest=packlib.ingest,
          on_progress: packlib.OnProgress = packlib._noop_progress) -> RepoBuildReport:
    """Ingère chaque source, route chaque fichier vers son dépôt de famille, écrit les dépôts.

    Ré-exécutable sans risque sur un `out_dir` déjà construit (les fichiers déjà présents sont
    remplacés, pas signalés en collision — une « collision » ne désigne QUE deux sources de CE
    même appel visant le même chemin cible). `ingest` est injectable (tests hors-réseau).
    Enregistre la configuration dans `<out_dir>/.repos-build.json` pour `update()`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir or (out_dir / ".work"))
    rep = RepoBuildReport(out_dir=out_dir, sources=list(sources))
    written: dict[str, dict[str, str]] = {}   # family -> {rel: sha1}, CE run uniquement

    for si, src in enumerate(sources):
        root = ingest(src, work_dir / f"src{si}", on_progress=on_progress)
        for f in _iter_files(root):
            rel = f.relative_to(root)
            cat, cid = packlib.classify_rel(str(rel))
            fam, target_rel = route(cat, cid, f.name, cards_as, split_cards_by_type)
            if fam is None:
                rep.unclassified.append({"source": src, "path": str(rel)})
                continue
            fam_written = written.setdefault(fam, {})
            if target_rel in fam_written:
                rep.collisions.append({"repo": fam, "rel": target_rel})
            dest = out_dir / fam / target_rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            fam_written[target_rel] = hashlib.sha1(dest.read_bytes()).hexdigest()

    for fam, files in written.items():
        stat = rep.repos.setdefault(fam, RepoStat(family=fam))
        stat.files = len(files)
        stat.bytes = sum((out_dir / fam / rel).stat().st_size for rel in files)
        stat.by_type = _by_type_from_written(files)
        _finalize_repo(out_dir / fam, stat, rep.sources, git_init, files)

    _record_build(out_dir, sources, cards_as, split_cards_by_type)
    return rep


def update(out_dir: Path, *, work_dir: Path | None = None, ingest=packlib.ingest,
          on_progress: packlib.OnProgress = packlib._noop_progress) -> list[RepoBuildReport]:
    """Rejoue tous les `build()` déjà lancés sous `out_dir` (mêmes sources, mêmes options),
    sans redemander les liens. Un rapport par configuration enregistrée (donc par jeu de
    familles produit) ; chaque `RepoStat` porte le diff depuis le build précédent."""
    entries = load_build_log(out_dir)
    if not entries:
        raise packlib.PackError(
            f"Aucun historique de build sous {out_dir} — lance d'abord "
            "`studio repos build <sources…> --out ...` au moins une fois.")
    return [build(e["sources"], out_dir, cards_as=e["cards_as"],
                 split_cards_by_type=e["split_cards_by_type"],
                 work_dir=work_dir, ingest=ingest, on_progress=on_progress)
           for e in entries]


def load_build_log(out_dir: Path) -> list[dict]:
    """Les configurations de build enregistrées sous `out_dir` (une par cards_as/split-type
    distincts). Fichier caché, en dehors de tous les dépôts de famille : jamais poussé."""
    path = Path(out_dir) / _LOG_NAME
    return json.loads(path.read_text()) if path.exists() else []


def _record_build(out_dir: Path, sources: list[str], cards_as: str,
                  split_cards_by_type: bool) -> None:
    path = Path(out_dir) / _LOG_NAME
    entries = json.loads(path.read_text()) if path.exists() else []
    entries = [e for e in entries
              if not (e["cards_as"] == cards_as
                      and e["split_cards_by_type"] == split_cards_by_type)]
    entries.append({"sources": list(sources), "cards_as": cards_as,
                    "split_cards_by_type": split_cards_by_type})
    path.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


_README = """# {fam} — dépôt d'images OPTCGSim (généré)

Généré par `optcgsim-studio repos build` à partir de {n} source(s). **Dépôt privé** : il
contient des images dont tu n'es pas l'ayant droit — ne le rends pas public.

Import dans le simulateur (via [optcgsim-studio](https://github.com/hquezser/optcgsim-studio)) :

```bash
studio packs add <url-de-ce-dépôt> --follow          # tout le dépôt
studio packs add <url-de-ce-dépôt> --only-type leader # granulaire (P8)
```

Mise à jour (nouveau set) : depuis le dossier PARENT de ce dépôt (celui passé en `--out` à la
génération), `studio repos update --out <ce-dossier-parent>` — rejoue les mêmes sources et
affiche ce qui a changé.

Contenu : voir `MANIFEST.json`.
"""


def _finalize_repo(repo_dir: Path, stat: RepoStat, sources: list[str], git_init: bool,
                   files: dict[str, str]) -> None:
    manifest_path = repo_dir / "MANIFEST.json"
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    old_files = old.get("files", {})
    stat.added = sorted(set(files) - set(old_files))
    stat.changed = sorted(r for r in files if r in old_files and old_files[r] != files[r])
    stat.orphans = sorted(set(old_files) - set(files))

    manifest_path.write_text(json.dumps({
        "family": stat.family, "generated_by": "optcgsim-studio repos build",
        "file_count": stat.files, "bytes": stat.bytes, "by_type": stat.by_type,
        "sources": sources, "files": files,
    }, indent=2, ensure_ascii=False))
    (repo_dir / "README.md").write_text(_README.format(fam=stat.family, n=len(sources)))
    if git_init:
        gi = repo_dir / ".gitignore"
        if not gi.exists():
            gi.write_text(".DS_Store\n.work/\n")
        if not (repo_dir / ".git").exists():
            try:
                subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True,
                               capture_output=True, timeout=30)
            except (OSError, subprocess.SubprocessError):
                pass   # git absent : les dossiers restent utilisables tels quels
