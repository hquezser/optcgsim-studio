"""Tests de la config locale + gestion du token GitHub (secret)."""

import json
import os
import stat
import sys

import pytest

from studio.config import ENV_TOKEN, Config, redact


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_TOKEN, raising=False)
    return Config(state_dir=tmp_path / "state")


def test_token_absent_by_default(cfg):
    assert cfg.github_token() is None
    assert cfg.has_github_token() is False


def test_set_and_read_token(cfg):
    cfg.set_github_token("ghp_secret123")
    assert cfg.github_token() == "ghp_secret123"
    assert cfg.has_github_token() is True


def test_token_persists_across_instances(cfg, tmp_path):
    cfg.set_github_token("ghp_persist")
    again = Config(state_dir=tmp_path / "state")
    assert again.github_token() == "ghp_persist"


def test_env_var_overrides_file(cfg, monkeypatch):
    cfg.set_github_token("ghp_fromfile")
    monkeypatch.setenv(ENV_TOKEN, "ghp_fromenv")
    assert cfg.github_token() == "ghp_fromenv"


def test_clear_token(cfg):
    cfg.set_github_token("x")
    cfg.set_github_token(None)
    assert cfg.github_token() is None


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Les bits de permission POSIX n'existent pas sur Windows : os.chmod n'y gère que "
           "l'attribut lecture-seule, et restreindre l'accès demanderait des ACL — donc une "
           "dépendance (pywin32) que le projet s'interdit. CONSÉQUENCE ASSUMÉE : sous "
           "Windows, le fichier de configuration contenant le token GitHub n'est PAS "
           "restreint au propriétaire.",
)
def test_config_file_is_owner_only(cfg):
    cfg.set_github_token("ghp_secret")
    mode = stat.S_IMODE(os.stat(cfg.path).st_mode)
    assert mode == 0o600           # secret : ni groupe ni autres


def test_token_not_in_plain_view_helper():
    # le serveur n'expose qu'un booléen — vérifié ici au niveau du helper redact
    assert redact("clone https://ghp_abc@github.com/x", "ghp_abc") == \
        "clone https://***@github.com/x"
    assert redact("message sans token", None) == "message sans token"


# ------------------------------------------------------------------ collection par défaut (P12)
def test_default_collection_source_absent_by_default(cfg):
    assert cfg.default_collection_source() is None


def test_set_and_read_default_collection_source(cfg):
    cfg.set_default_collection_source("/Users/x/optcgsim-repos/collection.json")
    assert cfg.default_collection_source() == "/Users/x/optcgsim-repos/collection.json"


def test_default_collection_source_persists_across_instances(cfg, tmp_path):
    cfg.set_default_collection_source("https://example/collection.json")
    again = Config(state_dir=tmp_path / "state")
    assert again.default_collection_source() == "https://example/collection.json"


def test_clear_default_collection_source(cfg):
    cfg.set_default_collection_source("x")
    cfg.set_default_collection_source(None)
    assert cfg.default_collection_source() is None
    cfg.set_default_collection_source("x")
    cfg.set_default_collection_source("")          # chaîne vide -> efface aussi
    assert cfg.default_collection_source() is None


def test_default_collection_source_coexists_with_token(cfg):
    cfg.set_github_token("ghp_secret")
    cfg.set_default_collection_source("/tmp/collection.json")
    assert cfg.github_token() == "ghp_secret"
    assert cfg.default_collection_source() == "/tmp/collection.json"
    # clairement pas un secret : PAS soumis au même traitement chmod-only-relevant
    data = json.loads(cfg.path.read_text(encoding="utf-8"))
    assert data["default_collection_source"] == "/tmp/collection.json"


# ------------------------------------------- P17.1 : variable d'environnement vide ≠ définie
def test_blank_env_token_does_not_mask_the_configured_one(tmp_path, monkeypatch):
    """Un `export OPTCG_STUDIO_GITHUB_TOKEN=` traînant dans un shell masquait le token du
    fichier : les dépôts privés échouaient avec un message d'authentification incompréhensible
    alors que le token était bien configuré."""
    from studio.config import ENV_TOKEN, Config
    cfg = Config(state_dir=tmp_path)
    cfg.set_github_token("ghp_du_fichier")
    for valeur_vide in ("", "   ", "\t\n"):
        monkeypatch.setenv(ENV_TOKEN, valeur_vide)
        assert cfg.github_token() == "ghp_du_fichier", (
            f"une variable vide ({valeur_vide!r}) doit être traitée comme non définie")
    monkeypatch.setenv(ENV_TOKEN, "  ghp_de_l_env  ")
    assert cfg.github_token() == "ghp_de_l_env", "une valeur réelle reste prioritaire"
