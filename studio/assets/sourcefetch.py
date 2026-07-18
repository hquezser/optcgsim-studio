"""Exploration + téléchargement SÉLECTIF d'une source distante (P7).

Objectif : ne récupérer QUE les fichiers voulus au lieu d'un zip monolithique. Mesuré sur un
vrai dépôt (repo FR) : 2,2 Go complet -> 39 Mo pour un seul set (98 % d'économie, disque ET
bande passante).

Capacité selon la source (reconnaissance 2026-07-18) :
  - GitHub : ✅ Tree API (structure + tailles en 1 requête) + API Contents (contenu par
    fichier). Marche sur dépôt PUBLIC (sans token) comme PRIVÉ (avec un PAT en en-tête).
  - Dropbox / zip direct / dossier local : ✗ pas d'exploration distante fiable (l'endpoint
    de listing Dropbox refuse les appels scriptés). `list_remote_files` renvoie None ->
    l'appelant retombe sur le téléchargement complet puis filtrage disque (packlib).

Ce module ne touche jamais au jeu ; il produit un dossier local (layout préservé) que
packlib normalise ensuite comme n'importe quelle source.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..nettls import CERT_FIX_HINT, is_cert_error, ssl_context

OnProgress = Callable[[str, int, int], None]


def _noop(phase: str, done: int, total: int) -> None:
    pass


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class RemoteFile:
    path: str            # chemin relatif dans le dépôt (ex. "FR_classique/OP01/OP01-003_OVERRIDE.png")
    size: int            # octets (0 si inconnu)


# --------------------------------------------------------------------------- GitHub
_GH_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+))?/?$")


def _gh_parts(url: str) -> tuple[str, str, str] | None:
    m = _GH_RE.match(url.strip())
    if not m:
        return None
    return m.group(1), m.group(2), (m.group(3) or "main")


def _gh_request(url: str, token: str | None) -> bytes:
    headers = {"User-Agent": "optcgsim-studio/0.1",
               "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_context()) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise FetchError("Accès GitHub refusé (401/403) — dépôt privé sans token valide, "
                             "ou token expiré/insuffisant (scope 'repo' requis).") from e
        if e.code == 404:
            raise FetchError("Dépôt ou branche introuvable (404) — si le dépôt est privé, "
                             "configure un token GitHub.") from e
        raise
    except urllib.error.URLError as e:
        if is_cert_error(e):
            raise FetchError(CERT_FIX_HINT) from e
        raise


def list_remote_files(source_url: str, token: str | None = None) -> list[RemoteFile] | None:
    """Liste (chemin, taille) de tous les fichiers d'une source, SANS rien télécharger.

    Renvoie None si la source ne supporte pas l'exploration distante (tout sauf GitHub) —
    l'appelant retombe alors sur le téléchargement complet.
    """
    parts = _gh_parts(source_url)
    if parts is None:
        return None
    owner, repo, branch = parts
    url = (f"https://api.github.com/repos/{owner}/{repo}"
           f"/git/trees/{branch}?recursive=1")
    data = json.loads(_gh_request(url, token))
    if data.get("truncated"):
        # dépôt énorme : la Tree API tronque à ~100k entrées. Aucun de nos cas d'usage
        # (packs d'assets) n'approche cette limite, mais on le signale plutôt que de mentir.
        raise FetchError("Arborescence trop grande pour l'API Tree (tronquée) — "
                         "utiliser le téléchargement complet pour cette source.")
    return [RemoteFile(t["path"], t.get("size", 0))
            for t in data.get("tree", []) if t.get("type") == "blob"]


def fetch_selected(source_url: str, paths: list[str], dest: Path,
                   token: str | None = None,
                   on_progress: OnProgress = _noop) -> Path:
    """Télécharge UNIQUEMENT `paths` (chemins relatifs du dépôt) dans `dest`, layout préservé.

    Contenu par fichier via l'API Contents (repli SÛR, documenté pour public ET privé :
    renvoie le contenu en base64). Renvoie `dest`.
    """
    parts = _gh_parts(source_url)
    if parts is None:
        raise FetchError(f"Source non explorable fichier-par-fichier : {source_url}")
    owner, repo, branch = parts
    dest = Path(dest)
    total = len(paths)
    for i, rel in enumerate(paths, 1):
        on_progress("download", i, total)
        api = (f"https://api.github.com/repos/{owner}/{repo}/contents/"
               f"{urllib.request.quote(rel)}?ref={branch}")
        meta = json.loads(_gh_request(api, token))
        content = meta.get("content")
        if content is None or meta.get("encoding") != "base64":
            # gros fichiers (>1 Mo) : l'API Contents renvoie sans contenu inline -> download_url
            durl = meta.get("download_url")
            if not durl:
                raise FetchError(f"Contenu indisponible pour {rel}")
            raw = _gh_request(durl, token)
        else:
            raw = base64.b64decode(content)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    return dest
