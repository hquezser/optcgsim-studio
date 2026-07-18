"""Configuration locale du studio — notamment le token GitHub (P7).

Le token GitHub (Personal Access Token) sert à explorer/télécharger sélectivement un dépôt
PRIVÉ (voir sourcefetch). C'est un SECRET : traité comme un mot de passe.
  - stocké en clair dans ~/.optcgsim-studio/config.json (chmod 600 à l'écriture), OU fourni
    par la variable d'environnement OPTCG_STUDIO_GITHUB_TOKEN (prioritaire) ;
  - JAMAIS renvoyé en clair par l'API (le serveur n'expose qu'un booléen « configuré ») ;
  - JAMAIS loggé ni inclus dans un rapport de job — voir redact() pour les messages d'erreur
    qui pourraient contenir une URL authentifiée ou le token lui-même.
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
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self.path)
        try:
            os.chmod(self.path, 0o600)      # secret : lisible par le seul propriétaire
        except OSError:
            pass

    # ------------------------------------------------------------ token GitHub
    def github_token(self) -> str | None:
        """Token effectif : variable d'environnement (prioritaire), sinon config.json."""
        env = os.environ.get(ENV_TOKEN)
        if env:
            return env.strip() or None
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


def redact(text: str, token: str | None) -> str:
    """Retire toute occurrence du token d'un message avant log/affichage/rapport."""
    if token and text:
        return text.replace(token, "***")
    return text
