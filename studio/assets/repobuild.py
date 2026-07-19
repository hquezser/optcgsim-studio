"""Constructeur de dépôts d'images (extension P8).

À partir de sources hétérogènes — **GitHub** (dépôt/zip), **Dropbox** (dossier partagé en
zip), **Google Drive** (fichier/zip partagé) — produit une arborescence **git-ready par
famille**, prête à pousser sur des dépôts PRIVÉS distincts :

    cards-alt/       Leaders/Cards/<SET>/<ID>.png, Events/…      (arts alternatifs)
    translations/    Leaders/Cards/… + TRANSLATION.txt           (cartes traduites)
    playmats/        Playmats/<nom>.png, background.jpg
    cardbacks-don/   CardBacks/<nom>.png, Cards/Don/Don.png

Pourquoi plusieurs dépôts (décision P8) : les images sont lourdes ; un dépôt par famille
reste sous les limites GitHub et se met à jour indépendamment. Les cartes sont **sous-classées
par type** (Leaders/Characters/Events/Stages/Unknown) via `cardmeta`, de sorte que :
  1. l'import granulaire P8 (`--only-type leader`) fonctionne directement sur le dépôt poussé ;
  2. si une famille dépasse la limite, on scinde en déplaçant un simple sous-dossier de type.

Le layout reste **compatible import** : chaque carte est sous un ancêtre `Cards/`, donc
`packlib.classify_rel` la reconnaît (le préfixe de type est ignoré à l'application, cf.
`_mirror_rel`). Ce module ne POUSSE rien : la publication reste une action de l'utilisateur
(on écrit les dossiers, un `git init` et un `.gitignore`, et on affiche la recette de push).
"""
from __future__ import annotations

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

# Familles à destination déterministe (indépendantes du tag `cards_as`).
_FIXED_FAMILY = {"don": "cardbacks-don", "cardbacks": "cardbacks-don",
                 "playmats": "playmats", "backgrounds": "playmats",
                 "translation": "translations"}

GITHUB_SOFT_LIMIT = 900 * 1024 * 1024   # ~900 Mo : au-delà, on conseille de scinder


@dataclass
class RepoStat:
    family: str
    files: int = 0
    bytes: int = 0
    by_type: dict = field(default_factory=dict)   # {TypeDir: n} pour les dépôts de cartes

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
        return "cardbacks-don", f"Cards/Don/{filename}"
    if cat in _FIXED_FAMILY:
        prefix = {"cardbacks": "CardBacks/", "playmats": "Playmats/"}.get(cat, "")
        return _FIXED_FAMILY[cat], f"{prefix}{filename}"
    return None, None


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if p.is_file():
            yield p


def build(sources: list[str], out_dir: Path, *, cards_as: str = "alt",
          split_cards_by_type: bool = True, git_init: bool = True,
          work_dir: Path | None = None, ingest=packlib.ingest,
          on_progress: packlib.OnProgress = packlib._noop_progress) -> RepoBuildReport:
    """Ingère chaque source, route chaque fichier vers son dépôt de famille, écrit les dépôts.

    `ingest` est injectable (tests hors-réseau). Dernière source gagnante en cas de collision
    (rapportée). N'écrit jamais hors de `out_dir`."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(work_dir or (out_dir / ".work"))
    rep = RepoBuildReport(out_dir=out_dir, sources=list(sources))

    for si, src in enumerate(sources):
        root = ingest(src, work_dir / f"src{si}", on_progress=on_progress)
        for f in _iter_files(root):
            rel = f.relative_to(root)
            cat, cid = packlib.classify_rel(str(rel))
            fam, target_rel = route(cat, cid, f.name, cards_as, split_cards_by_type)
            if fam is None:
                rep.unclassified.append({"source": src, "path": str(rel)})
                continue
            dest = out_dir / fam / target_rel
            if dest.exists():
                rep.collisions.append({"repo": fam, "rel": target_rel})
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
            stat = rep.repos.setdefault(fam, RepoStat(family=fam))
            stat.files += 1
            stat.bytes += dest.stat().st_size
            if cat == "cards":
                td = target_rel.split("/")[0] if split_cards_by_type else "Cards"
                stat.by_type[td] = stat.by_type.get(td, 0) + 1

    for fam, stat in rep.repos.items():
        _finalize_repo(out_dir / fam, stat, rep.sources, git_init)
    return rep


_README = """# {fam} — dépôt d'images OPTCGSim (généré)

Généré par `optcgsim-studio repos build` à partir de {n} source(s). **Dépôt privé** : il
contient des images dont tu n'es pas l'ayant droit — ne le rends pas public.

Import dans le simulateur (via [optcgsim-studio](https://github.com/hquezser/optcgsim-studio)) :

```bash
studio packs add <url-de-ce-dépôt> --follow          # tout le dépôt
studio packs add <url-de-ce-dépôt> --only-type leader # granulaire (P8)
```

Contenu : voir `MANIFEST.json`.
"""


def _finalize_repo(repo_dir: Path, stat: RepoStat, sources: list[str], git_init: bool) -> None:
    (repo_dir / "MANIFEST.json").write_text(json.dumps({
        "family": stat.family, "generated_by": "optcgsim-studio repos build",
        "files": stat.files, "bytes": stat.bytes, "by_type": stat.by_type,
        "sources": sources,
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
