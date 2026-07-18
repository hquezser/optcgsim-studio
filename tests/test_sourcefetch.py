"""Tests de sourcefetch (exploration Tree API + fetch sélectif) — hors-réseau (mock)."""

import base64
import json

import pytest

from studio.assets import sourcefetch as sf


# ------------------------------------------------------------------ parsing d'URL
def test_gh_parts_variants():
    assert sf._gh_parts("https://github.com/Owner/Repo") == ("Owner", "Repo", "main")
    assert sf._gh_parts("https://github.com/o/r/tree/dev") == ("o", "r", "dev")
    assert sf._gh_parts("https://github.com/o/r.git") == ("o", "r", "main")
    assert sf._gh_parts("https://www.dropbox.com/scl/fo/x") is None
    assert sf._gh_parts("/chemin/local") is None


def test_list_remote_files_none_for_non_github():
    assert sf.list_remote_files("https://www.dropbox.com/scl/fo/x") is None
    assert sf.list_remote_files("/un/dossier/local") is None


# ------------------------------------------------------------------ Tree API (mockée)
def test_list_remote_files_parses_tree(monkeypatch):
    tree = {"truncated": False, "tree": [
        {"path": "FR_classique/OP01/OP01-003_OVERRIDE.png", "type": "blob", "size": 1000},
        {"path": "FR_classique/OP01", "type": "tree"},           # dossier -> ignoré
        {"path": "TRANSLATION.txt", "type": "blob", "size": 50},
    ]}
    monkeypatch.setattr(sf, "_gh_request", lambda url, token: json.dumps(tree).encode())
    files = sf.list_remote_files("https://github.com/o/r")
    assert [f.path for f in files] == [
        "FR_classique/OP01/OP01-003_OVERRIDE.png", "TRANSLATION.txt"]
    assert files[0].size == 1000


def test_list_remote_files_truncated_raises(monkeypatch):
    monkeypatch.setattr(sf, "_gh_request",
                        lambda url, token: json.dumps({"truncated": True, "tree": []}).encode())
    with pytest.raises(sf.FetchError, match="tronquée"):
        sf.list_remote_files("https://github.com/o/r")


def test_token_passed_in_request(monkeypatch):
    seen = {}
    def fake(url, token):
        seen["token"] = token
        return json.dumps({"truncated": False, "tree": []}).encode()
    monkeypatch.setattr(sf, "_gh_request", fake)
    sf.list_remote_files("https://github.com/o/r", token="ghp_xyz")
    assert seen["token"] == "ghp_xyz"


# ------------------------------------------------------------------ fetch sélectif (mocké)
def test_fetch_selected_writes_only_requested(monkeypatch, tmp_path):
    def fake(url, token):
        # API Contents renvoie {content: base64, encoding: base64}
        payload = base64.b64encode(b"IMGDATA-" + url.encode()[-12:]).decode()
        return json.dumps({"content": payload, "encoding": "base64"}).encode()
    monkeypatch.setattr(sf, "_gh_request", fake)
    dest = sf.fetch_selected("https://github.com/o/r",
                             ["Cards/OP01/OP01-001.png", "TRANSLATION.txt"],
                             tmp_path / "out")
    assert (dest / "Cards" / "OP01" / "OP01-001.png").exists()
    assert (dest / "TRANSLATION.txt").exists()
    assert (dest / "Cards" / "OP01" / "OP01-001.png").read_bytes().startswith(b"IMGDATA-")
    # rien d'autre n'a été créé
    files = [p for p in (dest).rglob("*") if p.is_file()]
    assert len(files) == 2


def test_fetch_selected_large_file_falls_back_to_download_url(monkeypatch, tmp_path):
    def fake(url, token):
        if "download" in url or url.endswith(".png"):
            return b"RAWBYTES"
        # Contents sans contenu inline (fichier > 1 Mo) -> download_url
        return json.dumps({"content": None, "encoding": "none",
                           "download_url": "https://raw/x.png"}).encode()
    monkeypatch.setattr(sf, "_gh_request", fake)
    dest = sf.fetch_selected("https://github.com/o/r", ["big.png"], tmp_path / "o")
    assert (dest / "big.png").read_bytes() == b"RAWBYTES"


def test_fetch_selected_non_github_raises(tmp_path):
    with pytest.raises(sf.FetchError, match="non explorable"):
        sf.fetch_selected("https://dropbox.com/x", ["a.png"], tmp_path)
