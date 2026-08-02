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


# ------------------------------- P17.3 : URL pointant sur un sous-dossier du dépôt
def test_subfolder_url_is_still_recognised_as_github():
    """C'est l'URL qu'on copie après avoir navigué dans un dépôt sur github.com.

    Non reconnue, elle faisait abandonner l'exploration sélective et télécharger le dépôt
    ENTIER en zip — exactement l'économie que ce module existe pour obtenir.
    """
    from studio.assets.sourcefetch import _gh_parts, gh_subpath
    assert _gh_parts("https://github.com/o/r/tree/main/Cards/OP01") == ("o", "r", "main")
    assert gh_subpath("https://github.com/o/r/tree/main/Cards/OP01") == "Cards/OP01"
    # les formes déjà supportées ne régressent pas
    assert _gh_parts("https://github.com/o/r") == ("o", "r", "main")
    assert _gh_parts("https://github.com/o/r/tree/dev") == ("o", "r", "dev")
    assert _gh_parts("https://github.com/o/r.git") == ("o", "r", "main")
    assert gh_subpath("https://github.com/o/r/tree/main") is None
    assert _gh_parts("https://gitlab.com/o/r") is None


# ------------------------------------------ P15.7 : tolérance aux échecs partiels (cartes alt)
def test_fetch_selected_tolerant_skips_failing_files_and_keeps_the_rest(monkeypatch, tmp_path):
    """Cas réel visé : un dépôt de cartes alternatives où quelques fichiers sont
    temporairement indisponibles (404, coupure) ne doit PAS faire perdre tout le reste — la
    carte non récupérée garde simplement son art d'origine dans le jeu."""
    def fake(url, token):
        if "casse.png" in url:
            raise sf.FetchError("simulé : 404 transitoire")
        return b"OK-" + url.encode()[-8:]
    monkeypatch.setattr(sf, "_gh_request", fake)

    echecs: list[dict] = []
    dest = sf.fetch_selected("https://github.com/o/r",
                             ["Cards/OP01/bon1.png", "Cards/OP01/casse.png",
                              "Cards/OP01/bon2.png"],
                             tmp_path / "out", strict=False, failed=echecs)

    assert (dest / "Cards" / "OP01" / "bon1.png").exists()
    assert (dest / "Cards" / "OP01" / "bon2.png").exists()
    assert not (dest / "Cards" / "OP01" / "casse.png").exists()
    assert [e["path"] for e in echecs] == ["Cards/OP01/casse.png"]
    assert echecs[0]["reason"]


def test_fetch_selected_tolerant_raises_if_nothing_succeeds(monkeypatch, tmp_path):
    """Un pack vide en silence serait pire qu'une erreur explicite : si RIEN n'a pu être
    récupéré, `strict=False` échoue quand même."""
    monkeypatch.setattr(sf, "_gh_request",
                        lambda url, token: (_ for _ in ()).throw(
                            sf.FetchError("simulé : tout échoue")))
    with pytest.raises(sf.FetchError):
        sf.fetch_selected("https://github.com/o/r", ["a.png", "b.png"], tmp_path / "out",
                          strict=False, failed=[])


def test_fetch_selected_strict_still_aborts_on_first_failure(monkeypatch, tmp_path):
    """`strict=True` (défaut) : comportement historique préservé — c'est ce qu'exige
    `_repair_corrupted` (un dépôt réparé à moitié n'est pas réparé)."""
    def fake(url, token):
        if "casse.png" in url:
            raise sf.FetchError("simulé")
        return b"OK"
    monkeypatch.setattr(sf, "_gh_request", fake)
    with pytest.raises(sf.FetchError):
        sf.fetch_selected("https://github.com/o/r",
                         ["bon.png", "casse.png", "jamais-tente.png"], tmp_path / "out")
    assert not (tmp_path / "out" / "jamais-tente.png").exists(), (
        "en strict, l'arrêt doit être immédiat — le fichier suivant n'est pas tenté")


def test_fetch_selected_tolerant_recovers_from_raw_urlerror_not_just_fetcherror(
        monkeypatch, tmp_path):
    """Avant : seul `FetchError` déclenchait le repli CDN -> API Contents. Une coupure réseau
    brute (URLError non enveloppée) abandonnait le fichier sans même tenter le repli."""
    import urllib.error
    appels = []

    def fake(url, token):
        appels.append(url)
        if "raw.githubusercontent.com" in url:
            raise urllib.error.URLError("simulé : coupure réseau brute (pas un FetchError)")
        return json.dumps({"content": base64.b64encode(b"VIA-CONTENTS-API").decode(),
                           "encoding": "base64"}).encode()

    monkeypatch.setattr(sf, "_gh_request", fake)
    dest = sf.fetch_selected("https://github.com/o/r", ["Cards/OP01/OP01-001.png"],
                             tmp_path / "out")
    assert (dest / "Cards" / "OP01" / "OP01-001.png").read_bytes() == b"VIA-CONTENTS-API"
    assert len(appels) == 2, "le CDN a échoué, l'API Contents doit avoir été tentée ensuite"
