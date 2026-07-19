"""Tests de sourcefetch (exploration Tree API + fetch sélectif) — hors-réseau (mock)."""

import base64
import json
import urllib.error

import pytest

from studio.assets import sourcefetch as sf


def _http_error(code, reason, headers=None):
    return urllib.error.HTTPError("http://x", code, reason, headers or {}, None)


# ------------------------------------------------------------------ classification 401/403
# Incident réel (2026-07-19) : un dépôt PUBLIC (Sparklight-TL/OPTCGSim_FR) a renvoyé un 403,
# pris à tort pour un refus d'accès ("dépôt privé sans token") -- c'était la limite de
# requêtes GitHub (60/heure sans auth), épuisée par le fetch fichier-par-fichier d'un dossier
# de plusieurs centaines de cartes. Le message doit distinguer les deux cas.
def test_is_rate_limited_via_header():
    assert sf._is_rate_limited(_http_error(403, "Forbidden", {"X-RateLimit-Remaining": "0"}))


def test_is_rate_limited_via_reason_phrase():
    assert sf._is_rate_limited(_http_error(403, "rate limit exceeded"))


def test_is_rate_limited_false_for_real_auth_error():
    assert not sf._is_rate_limited(_http_error(403, "Forbidden", {"X-RateLimit-Remaining": "59"}))
    assert not sf._is_rate_limited(_http_error(401, "Bad credentials"))


def test_gh_request_rate_limited_gives_actionable_message(monkeypatch):
    def fake_urlopen(req, timeout=30, context=None):
        raise _http_error(403, "rate limit exceeded")
    monkeypatch.setattr(sf.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(sf.FetchError, match="Limite de requêtes"):
        sf._gh_request("https://api.github.com/x", None)


def test_gh_request_real_auth_error_keeps_private_repo_message(monkeypatch):
    def fake_urlopen(req, timeout=30, context=None):
        raise _http_error(403, "Forbidden", {"X-RateLimit-Remaining": "59"})
    monkeypatch.setattr(sf.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(sf.FetchError, match="dépôt privé"):
        sf._gh_request("https://api.github.com/x", None)


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
# Contenu par fichier via raw.githubusercontent.com EN PRIORITÉ (pas l'API Contents, plafonnée
# à 60 req/heure sans auth — épuisée en usage réel par un dossier de quelques centaines de
# cartes, cf. incident 2026-07-19). Le mock distingue les deux endpoints pour vérifier lequel
# est réellement appelé, pas juste la valeur finale (qui pourrait coïncider par accident).
def test_fetch_selected_uses_raw_cdn_not_contents_api(monkeypatch, tmp_path):
    calls = []

    def fake(url, token):
        calls.append(url)
        assert "raw.githubusercontent.com" in url, "l'API Contents ne doit PAS être appelée"
        return b"IMGDATA-" + url.encode()[-12:]   # le CDN renvoie les octets bruts, sans JSON

    monkeypatch.setattr(sf, "_gh_request", fake)
    dest = sf.fetch_selected("https://github.com/o/r",
                             ["Cards/OP01/OP01-001.png", "TRANSLATION.txt"],
                             tmp_path / "out")
    assert (dest / "Cards" / "OP01" / "OP01-001.png").exists()
    assert (dest / "TRANSLATION.txt").exists()
    assert (dest / "Cards" / "OP01" / "OP01-001.png").read_bytes().startswith(b"IMGDATA-")
    assert len(calls) == 2   # un appel CDN par fichier, aucun repli
    # rien d'autre n'a été créé
    files = [p for p in (dest).rglob("*") if p.is_file()]
    assert len(files) == 2


def test_fetch_selected_falls_back_to_contents_api_when_cdn_fails(monkeypatch, tmp_path):
    """Si raw.githubusercontent.com échoue pour un chemin (édge-case, dépôt privé…), repli sur
    l'API Contents — comportement historique, préservé."""
    def fake(url, token):
        if "raw.githubusercontent.com" in url:
            raise sf.FetchError("simulé : le CDN a échoué pour ce chemin")
        payload = base64.b64encode(b"FROM-CONTENTS-API").decode()
        return json.dumps({"content": payload, "encoding": "base64"}).encode()

    monkeypatch.setattr(sf, "_gh_request", fake)
    dest = sf.fetch_selected("https://github.com/o/r", ["Cards/OP01/OP01-001.png"], tmp_path / "o")
    assert (dest / "Cards" / "OP01" / "OP01-001.png").read_bytes() == b"FROM-CONTENTS-API"


def test_fetch_selected_large_file_falls_back_to_download_url(monkeypatch, tmp_path):
    """CDN en échec ET fichier > 1 Mo (l'API Contents ne renvoie pas le contenu inline) ->
    second repli sur `download_url`."""
    def fake(url, token):
        if "raw.githubusercontent.com" in url:
            raise sf.FetchError("simulé : le CDN a échoué pour ce chemin")
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
