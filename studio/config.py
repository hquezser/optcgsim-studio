"""Configuration locale du studio — le token GitHub (P7) et la collection par défaut (P12).

Le token GitHub (Personal Access Token) sert à explorer/télécharger sélectivement un dépôt
PRIVÉ (voir sourcefetch). C'est un SECRET : traité comme un mot de passe.
  - stocké en clair dans ~/.optcgsim-studio/config.json (chmod 600 à l'écriture), OU fourni
    par la variable d'environnement OPTCG_STUDIO_GITHUB_TOKEN (prioritaire) ;
  - JAMAIS renvoyé en clair par l'API (le serveur n'expose qu'un booléen « configuré ») ;
  - JAMAIS loggé ni inclus dans un rapport de job — voir redact() pour les messages d'erreur
    qui pourraient contenir une URL authentifiée ou le token lui-même.

`default_collection_source` (P12) N'est PAS un secret — un chemin local ou une URL de
`collection.json` (P10) à auto-analyser à l'ouverture de `studio ui`, pour proposer ses
propres packs sans avoir à recoller la source à chaque fois. Renvoyée en clair par l'API.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_DIR = Path.home() / ".optcgsim-studio"
ENV_TOKEN = "OPTCG_STUDIO_GITHUB_TOKEN"


class Config:
    def __init__(self, state_dir: Path = DEFAULT_DIR):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "config.json"

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)      # secret : lisible par le seul propriétaire
        except OSError:
            pass

    # ------------------------------------------------------------ token GitHub
    def github_token(self) -> str | None:
        """Token effectif : variable d'environnement (prioritaire), sinon config.json."""
        # On teste la valeur APRÈS nettoyage : une variable définie mais vide (ou ne contenant
        # que des espaces — cas courant d'un `export OPTCG_STUDIO_GITHUB_TOKEN=` traînant dans
        # un shell) masquait le token du fichier et faisait échouer les dépôts privés avec un
        # message d'authentification incompréhensible. Vide = « non définie ».
        env = (os.environ.get(ENV_TOKEN) or "").strip()
        if env:
            return env
        tok = self._read().get("github_token")
        return tok.strip() if isinstance(tok, str) and tok.strip() else None

    def set_github_token(self, token: str | None) -> None:
        data = self._read()
        if token:
            data["github_token"] = token.strip()
        else:
            data.pop("github_token", None)      # None/"" -> efface
        self._write(data)

    def has_github_token(self) -> bool:
        return self.github_token() is not None

    # ------------------------------------------------------------ collection par défaut (P12)
    def default_collection_source(self) -> str | None:
        """Source (chemin local ou URL) d'un `collection.json` (P10) à auto-analyser à
        l'ouverture de `studio ui` — PAS un secret, renvoyée en clair (contrairement au token)."""
        src = self._read().get("default_collection_source")
        return src.strip() if isinstance(src, str) and src.strip() else None

    def set_default_collection_source(self, source: str | None) -> None:
        data = self._read()
        if source and source.strip():
            data["default_collection_source"] = source.strip()
        else:
            data.pop("default_collection_source", None)     # None/"" -> efface
        self._write(data)


def redact(text: str, token: str | None) -> str:
    """Retire toute occurrence du token d'un message avant log/affichage/rapport."""
    if token and text:
        return text.replace(token, "***")
    return text
