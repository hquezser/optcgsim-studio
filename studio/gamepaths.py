"""Localisation cross-OS de l'installation OPTCGSim et de ses surfaces d'assets.

Cartographie établie par inspection RÉELLE d'une installation macOS (sim 1.41b) — les
chemins Windows/Linux suivent les conventions Unity standard et sont à confirmer sur
machine réelle (marqués `verified=False`).

Deux surfaces d'assets distinctes, TOUTES en fichiers libres (aucun bundle Unity à toucher) :

1. STREAMING ASSETS (dans le bundle/dossier de l'app) — assets de base :
     StreamingAssets/Cards/<SET>/<ID>.png        image de carte plein format (480×671)
     StreamingAssets/Cards/<SET>/<ID>_small.jpg  miniature (deck builder)
     StreamingAssets/Playmats/<Nom>.png          tapis de jeu
     StreamingAssets/CardBacks/CardBackRegular.png / CardBackDon.png
     StreamingAssets/background.jpg / deckeditbackground.jpg
     StreamingAssets/TRANSLATION.txt             localisation (format Clé=Valeur)

2. CACHE VERSIONNÉ (données utilisateur Unity) — sets récents téléchargés par le sim :
     <persistent>/<version>/Cards/<ID>.jpg       (ex. 1.41b/Cards/OP17-062.jpg, 480×669)

⚠ macOS : l'app est SIGNÉE ; modifier StreamingAssets invalide la signature du bundle.
En pratique une app déjà autorisée se relance, mais le gestionnaire d'assets impose
backup+manifeste et sait tout restaurer à l'identique. Une mise à jour du sim écrase les
swaps (nouveau bundle / nouveau dossier de version) -> les packs restent stockés côté
studio et se ré-appliquent en une commande.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GameInstall:
    """Chemins résolus d'une installation. `verified` = cartographie confirmée sur machine."""
    app_root: Path            # racine app (macOS : le .app ; Win/Linux : dossier du jeu)
    streaming_assets: Path    # assets de base (fichiers libres)
    persistent: Path          # données utilisateur Unity (decks .txt, cache versionné, logs)
    os_name: str
    verified: bool

    @property
    def cards_dir(self) -> Path:
        return self.streaming_assets / "Cards"

    @property
    def playmats_dir(self) -> Path:
        return self.streaming_assets / "Playmats"

    @property
    def cardbacks_dir(self) -> Path:
        return self.streaming_assets / "CardBacks"

    @property
    def translation_file(self) -> Path:
        return self.streaming_assets / "TRANSLATION.txt"

    def version_dirs(self) -> list[Path]:
        """Dossiers de cache versionnés (1.41b, …), du plus récent au plus ancien."""
        if not self.persistent.exists():
            return []
        out = [p for p in self.persistent.iterdir()
               if p.is_dir() and p.name[:1].isdigit() and (p / "Cards").exists()]
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    def find_card_files(self, card_id: str) -> list[Path]:
        """Tous les fichiers image existants pour une carte (base + miniature + caches).

        Une carte peut vivre : dans StreamingAssets/Cards/<SET>/ (png + _small.jpg) ET/OU
        dans un ou plusieurs caches versionnés (jpg). Un swap complet remplace TOUS.
        """
        set_code = card_id.split("-")[0]
        cands = [
            self.cards_dir / set_code / f"{card_id}.png",
            self.cards_dir / set_code / f"{card_id}_small.jpg",
        ]
        cands += [vd / "Cards" / f"{card_id}.jpg" for vd in self.version_dirs()]
        return [p for p in cands if p.exists()]


def _macos() -> GameInstall | None:
    app = Path("/Applications/OPTCGSim.app")
    home_app = Path.home() / "Applications" / "OPTCGSim.app"
    root = app if app.exists() else home_app if home_app.exists() else None
    if root is None:
        return None
    return GameInstall(
        app_root=root,
        streaming_assets=root / "Contents" / "Resources" / "Data" / "StreamingAssets",
        persistent=Path.home() / "Library" / "Application Support" / "com.Batsu.OPTCGSim",
        os_name="macos", verified=True,
    )


def _windows() -> GameInstall | None:
    # Conventions Unity standard (à confirmer sur machine réelle).
    import os
    candidates = [Path(p) for p in (
        os.environ.get("OPTCGSIM_HOME", ""),
        r"C:\Program Files\OPTCGSim", r"C:\OPTCGSim",
        str(Path.home() / "Desktop" / "OPTCGSim"),
        str(Path.home() / "Downloads" / "OPTCGSim"),
    ) if p]
    root = next((c for c in candidates if (c / "OPTCGSim_Data").exists()), None)
    if root is None:
        return None
    return GameInstall(
        app_root=root,
        streaming_assets=root / "OPTCGSim_Data" / "StreamingAssets",
        persistent=Path.home() / "AppData" / "LocalLow" / "Batsu" / "OPTCGSim",
        os_name="windows", verified=False,
    )


def _linux() -> GameInstall | None:
    import os
    candidates = [Path(p) for p in (
        os.environ.get("OPTCGSIM_HOME", ""),
        str(Path.home() / "OPTCGSim"), str(Path.home() / "Games" / "OPTCGSim"),
    ) if p]
    root = next((c for c in candidates if (c / "OPTCGSim_Data").exists()), None)
    if root is None:
        return None
    return GameInstall(
        app_root=root,
        streaming_assets=root / "OPTCGSim_Data" / "StreamingAssets",
        persistent=Path.home() / ".config" / "unity3d" / "Batsu" / "OPTCGSim",
        os_name="linux", verified=False,
    )


def locate(app_root: Path | None = None) -> GameInstall | None:
    """Localise l'installation du sim. `app_root` force un emplacement explicite."""
    if app_root is not None:
        ar = Path(app_root)
        if ar.suffix == ".app" or (ar / "Contents").exists():   # bundle macOS
            sa = ar / "Contents" / "Resources" / "Data" / "StreamingAssets"
            persistent = (Path.home() / "Library" / "Application Support"
                          / "com.Batsu.OPTCGSim")
            return GameInstall(ar, sa, persistent, "macos", verified=sa.exists())
        sa = ar / "OPTCGSim_Data" / "StreamingAssets"
        persistent = Path.home() / "AppData" / "LocalLow" / "Batsu" / "OPTCGSim"
        return GameInstall(ar, sa, persistent, platform.system().lower(),
                           verified=sa.exists())
    return {"Darwin": _macos, "Windows": _windows, "Linux": _linux}.get(
        platform.system(), lambda: None)()
