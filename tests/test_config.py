"""Tests de la config locale + gestion du token GitHub (secret)."""

import json
import os
import stat

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


def test_config_file_is_owner_only(cfg):
    cfg.set_github_token("ghp_secret")
    mode = stat.S_IMODE(os.stat(cfg.path).st_mode)
    assert mode == 0o600           # secret : ni groupe ni autres


def test_token_not_in_plain_view_helper():
    # le serveur n'expose qu'un booléen — vérifié ici au niveau du helper redact
    assert redact("clone https://ghp_abc@github.com/x", "ghp_abc") == \
        "clone https://***@github.com/x"
    assert redact("message sans token", None) == "message sans token"
