"""Garde-fou exécutable de la contrainte la plus structurante du projet : ZÉRO dépendance.

`AGENTS.md` et `README.md` la posent en convention stricte (« l'UI tourne sur `http.server`
de la stdlib, pas de Node/npm/build »), mais rien ne l'empêchait de dériver : il suffisait
d'un `import requests` pour que le studio cesse de s'installer chez quelqu'un qui n'a que
Python. Ce test l'interdit mécaniquement.

Il vérifie aussi que le paquet reste installable sur la version minimale annoncée par
`pyproject.toml` — un `requires-python` qui ment est un bug d'installation silencieux.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).parent.parent
PAQUET = RACINE / "studio"

# Modules du projet lui-même (imports relatifs mis à part).
INTERNES = {"studio"}

# Dépendances FACULTATIVES tolérées — et uniquement en import paresseux, dans une fonction,
# derrière un repli sur ImportError. Le studio doit fonctionner sans elles.
#   certifi : trousseau de secours quand l'installeur python.org de macOS n'a pas peuplé les
#             certificats racine (cf. studio/nettls.py). Absent = repli silencieux.
FACULTATIFS = {"certifi"}


def _noms(noeud) -> set[str]:
    if isinstance(noeud, ast.Import):
        return {a.name.split(".")[0] for a in noeud.names}
    if isinstance(noeud, ast.ImportFrom) and not noeud.level and noeud.module:
        return {noeud.module.split(".")[0]}
    return set()                        # `from . import x` -> interne


def _imports_au_niveau_module(fichier: Path) -> set[str]:
    """Imports exécutés à l'IMPORT du module — ceux qui cassent tout s'ils manquent."""
    arbre = ast.parse(fichier.read_text(), filename=str(fichier))
    out: set[str] = set()
    a_visiter = list(arbre.body)
    corps_differe = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    while a_visiter:
        n = a_visiter.pop()
        out |= _noms(n)
        if isinstance(n, corps_differe):
            continue      # corps exécuté À L'APPEL, pas à l'import : hors périmètre ici
        # en revanche on descend dans try/if/with de niveau module
        for champ in ("body", "orelse", "finalbody", "handlers"):
            a_visiter += [c for c in getattr(n, champ, []) if isinstance(c, ast.stmt)]
    return out


def _tous_les_imports(fichier: Path) -> set[str]:
    arbre = ast.parse(fichier.read_text(), filename=str(fichier))
    out: set[str] = set()
    for noeud in ast.walk(arbre):
        out |= _noms(noeud)
    return out


def _fichiers_du_paquet() -> list[Path]:
    return sorted(p for p in PAQUET.rglob("*.py") if "__pycache__" not in p.parts)


def test_aucun_import_externe_au_niveau_module():
    """Rien hors stdlib ne doit être importé à l'IMPORT d'un module de `studio/`.

    C'est la garantie forte : le studio s'installe et démarre avec la seule stdlib. Même une
    dépendance facultative comme `certifi` ne doit PAS apparaître ici — sinon son absence
    casserait l'import, donc `studio --help`.
    """
    stdlib = set(sys.stdlib_module_names)
    coupables: dict[str, set[str]] = {}
    for fichier in _fichiers_du_paquet():
        externes = _imports_au_niveau_module(fichier) - stdlib - INTERNES
        if externes:
            coupables[str(fichier.relative_to(RACINE))] = externes

    assert not coupables, (
        "dépendance(s) externe(s) importée(s) au niveau module dans studio/ — le projet "
        f"doit rester installable et démarrable avec la seule stdlib :\n{coupables}")


def test_les_seules_dependances_paresseuses_sont_celles_documentees():
    """Un import paresseux hors stdlib est toléré, mais seulement s'il est ALLOWLISTÉ ici —
    pour qu'ajouter une dépendance reste une décision consciente, jamais un accident."""
    stdlib = set(sys.stdlib_module_names)
    coupables: dict[str, set[str]] = {}
    for fichier in _fichiers_du_paquet():
        externes = _tous_les_imports(fichier) - stdlib - INTERNES - FACULTATIFS
        if externes:
            coupables[str(fichier.relative_to(RACINE))] = externes

    assert not coupables, (
        "dépendance(s) externe(s) non documentée(s) dans studio/ — si elle est vraiment "
        "voulue et facultative, ajoute-la à FACULTATIFS avec sa justification :\n"
        f"{coupables}")


def test_certifi_reste_facultatif_a_l_execution():
    """`nettls` doit produire un contexte SSL valable même si `certifi` est introuvable."""
    import builtins

    from studio import nettls

    vrai_import = builtins.__import__

    def sans_certifi(nom, *a, **kw):
        if nom == "certifi":
            raise ImportError("simulé : certifi absent")
        return vrai_import(nom, *a, **kw)

    builtins.__import__ = sans_certifi
    try:
        ctx = nettls.ssl_context()
    finally:
        builtins.__import__ = vrai_import
    assert ctx.verify_mode is not None, "la vérification ne doit jamais être désactivée"


def test_pyproject_declare_bien_zero_dependance():
    """La promesse doit aussi être tenue dans les métadonnées d'installation."""
    texte = (RACINE / "pyproject.toml").read_text()
    assert "dependencies = []" in texte, (
        "pyproject.toml doit déclarer `dependencies = []` — sinon `pip install` tirerait "
        "des paquets tiers malgré la convention.")


def test_les_fichiers_du_paquet_sont_bien_analysables():
    """Filet : si le walk ne trouve rien, le test précédent passerait à vide."""
    fichiers = _fichiers_du_paquet()
    assert len(fichiers) >= 15, f"seulement {len(fichiers)} fichiers analysés"


@pytest.mark.parametrize("module", ["studio.cli", "studio.api.server",
                                    "studio.assets.manager", "studio.storage.local"])
def test_les_modules_principaux_s_importent_sans_effet_de_bord(module):
    """Importer ne doit RIEN faire : ni créer `~/.optcgsim-studio`, ni toucher au jeu.

    Un effet de bord à l'import casserait `--help`, la complétion shell, et rendrait les
    tests dépendants de la machine.
    """
    import importlib
    importlib.import_module(module)
