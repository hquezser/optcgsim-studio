"""Exploration + téléchargement SÉLECTIF d'une source distante (P7).

Objectif : ne récupérer QUE les fichiers voulus au lieu d'un zip monolithique. Mesuré sur un
vrai dépôt (repo FR) : 2,2 Go complet -> 39 Mo pour un seul set (98 % d'économie, disque ET
bande passante).

Capacité selon la source (reconnaissance 2026-07-18) :
  - GitHub : ✅ Tree API (structure + tailles en 1 requête, api.github.com) + contenu par
    fichier via `raw.githubusercontent.com` (CDN — PAS l'API REST, plafonnée à 60 req/heure
    sans authentification : un dossier de quelques centaines de fichiers l'épuise en une
    seule commande, rencontré en usage réel sur un dépôt PUBLIC ; cf. incident 2026-07-19).
    Repli sur l'API Contents (api.github.com) si le CDN échoue pour un chemin. Marche sur
    dépôt PUBLIC (sans token) comme PRIVÉ (avec un PAT en en-tête).
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
# Le `(?:/(.*))?` final accepte une URL pointant sur un SOUS-DOSSIER — exactement ce que l'on
# copie depuis GitHub après avoir navigué dans le dépôt (…/tree/main/Cards/OP01). Sans lui, ces
# URL n'étaient pas reconnues comme GitHub : l'exploration sélective était abandonnée et le
# dépôt ENTIER téléchargé en zip, perdant l'économie de bande passante qui justifie ce module.
_GH_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/tree/([^/]+)(?:/(.*))?)?/?$")


def _gh_parts(url: str) -> tuple[str, str, str] | None:
    """(propriétaire, dépôt, branche). Un éventuel sous-dossier dans l'URL est accepté mais
    pas retourné : le périmètre se règle par `--path-prefix`, et reconnaître l'URL suffit à
    garder le fetch sélectif au lieu de retomber sur le zip complet du dépôt."""
    m = _GH_RE.match(url.strip())
    if not m:
        return None
    return m.group(1), m.group(2), (m.group(3) or "main")


def gh_subpath(url: str) -> str | None:
    """Sous-dossier ciblé par une URL GitHub, s'il y en a un (« Cards/OP01 »)."""
    m = _GH_RE.match(url.strip())
    return (m.group(4) or None) if m else None


def _is_rate_limited(e: urllib.error.HTTPError) -> bool:
    """GitHub renvoie aussi un 403 pour la limite de requêtes (60/heure SANS authentification,
    5000/heure avec un token même sans permission particulière) — à ne pas confondre avec un
    vrai refus d'accès (dépôt privé). Détecté via l'en-tête `X-RateLimit-Remaining: 0` (fiable)
    ou, à défaut, la phrase de raison HTTP que GitHub fixe à « rate limit exceeded »."""
    try:
        if e.headers is not None and e.headers.get("X-RateLimit-Remaining") == "0":
            return True
    except AttributeError:
        pass
    return "rate limit" in (e.reason or "").lower()


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
            if _is_rate_limited(e):
                raise FetchError(
                    "Limite de requêtes GitHub atteinte (60/heure SANS authentification, "
                    "quel que soit le dépôt — public ou privé). Configure un token "
                    "(`studio config set-github-token <PAT>`, aucune permission particulière "
                    "requise pour un dépôt public) pour passer à 5000/heure.") from e
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


def _fetch_one_file(owner: str, repo: str, branch: str, rel: str, token: str | None) -> bytes:
    """Octets d'UN fichier du dépôt. CDN `raw.githubusercontent.com` en priorité — PAS l'API
    REST `api.github.com`, plafonnée à 60 requêtes/heure SANS authentification (un dossier de
    quelques centaines de cartes l'épuise en une seule commande ; rencontré en usage réel sur
    le vrai repo FR, dépôt PUBLIC pourtant). Repli sur l'API Contents UNIQUEMENT si le CDN
    échoue pour CE chemin (édge-case, ou dépôt privé si le CDN ne l'honore pas).

    Le repli réagit à `URLError` en plus de `FetchError` : une simple coupure réseau sur le
    CDN (pas seulement un refus HTTP) doit aussi donner sa chance à l'API Contents — avant,
    seul `FetchError` déclenchait le repli, une erreur réseau brute abandonnait direct.
    """
    raw_url = (f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/"
              f"{urllib.request.quote(rel)}")
    try:
        return _gh_request(raw_url, token)
    except (FetchError, urllib.error.URLError):
        pass   # repli sur l'API Contents ci-dessous
    api = (f"https://api.github.com/repos/{owner}/{repo}/contents/"
          f"{urllib.request.quote(rel)}?ref={branch}")
    meta = json.loads(_gh_request(api, token))
    content = meta.get("content")
    if content is None or meta.get("encoding") != "base64":
        # gros fichiers (>1 Mo) : l'API Contents renvoie sans contenu inline -> download_url
        durl = meta.get("download_url")
        if not durl:
            raise FetchError(f"Contenu indisponible pour {rel}")
        return _gh_request(durl, token)
    return base64.b64decode(content)


def fetch_selected(source_url: str, paths: list[str], dest: Path,
                   token: str | None = None,
                   on_progress: OnProgress = _noop,
                   strict: bool = True,
                   failed: list[dict] | None = None) -> Path:
    """Télécharge UNIQUEMENT `paths` (chemins relatifs du dépôt) dans `dest`, layout préservé.
    Renvoie `dest`.

    `strict` (par défaut `True`, comportement historique) : la PREMIÈRE erreur sur un fichier
    fait échouer tout l'appel. C'est ce qu'exige `_repair_corrupted` (packlib) : un dépôt
    « réparé » à moitié n'est pas réparé, mieux vaut retomber sur un téléchargement complet.

    `strict=False` (import sélectif normal, `repos build --path-prefix`) : un fichier en échec
    est SAUTÉ et consigné dans `failed` (si fourni, `{"path", "reason"}`) — les autres
    continuent. N'échoue que si RIEN n'a pu être récupéré (`paths` non vide, zéro succès) :
    un pack vide en silence serait pire qu'une erreur explicite. C'est le cas réel visé : un
    dépôt de milliers de cartes alternatives où une poignée de fichiers est temporairement
    indisponible ne doit pas faire perdre tout le reste — l'art d'origine reste en place pour
    les cartes non récupérées, réversible en relançant l'import plus tard.
    """
    parts = _gh_parts(source_url)
    if parts is None:
        raise FetchError(f"Source non explorable fichier-par-fichier : {source_url}")
    owner, repo, branch = parts
    dest = Path(dest)
    total = len(paths)
    reussis = 0
    for i, rel in enumerate(paths, 1):
        on_progress("download", i, total)
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            out.write_bytes(_fetch_one_file(owner, repo, branch, rel, token))
            reussis += 1
        except (FetchError, urllib.error.URLError) as e:
            if strict:
                raise
            if failed is not None:
                failed.append({"path": rel, "reason": str(e)})
    if not strict and paths and reussis == 0:
        raise FetchError(f"Aucun des {len(paths)} fichier(s) n'a pu être récupéré — "
                         "vérifie la connexion ou le token GitHub.")
    return dest
