"""Tests de l'API studio : service métier + serveur HTTP (stdlib, en thread)."""

import json
import struct
import threading
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from studio.api.server import StudioService, make_handler
from studio.gamepaths import GameInstall


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))
    return path


@pytest.fixture()
def svc(tmp_path):
    sa = tmp_path / "app" / "StreamingAssets"
    (sa / "Cards" / "OP01").mkdir(parents=True)
    (sa / "Playmats").mkdir(parents=True)
    make_png(sa / "Cards" / "OP01" / "OP01-001.png")
    make_png(sa / "Cards" / "OP01" / "OP01-016.png")
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    inst = GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)
    (tmp_path / "persist").mkdir()
    return StudioService(inst, db_path=str(tmp_path / "studio.db"),
                         lib_dir=tmp_path / "lib", state_dir=tmp_path / "state")


def _pack(root: Path):
    make_png(root / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    make_png(root / "Playmats" / "Blue.png", 2560, 1440)
    return root


# ------------------------------------------------------------------ service métier
def test_inventory(svc):
    inv = svc.inventory()
    assert inv["verified"] is True and "OP01" in inv["card_sets"]


def test_add_apply_remove_cycle(svc, tmp_path):
    svc.add_source(str(_pack(tmp_path / "src")), name="Theme")
    assert svc.packs()[0]["name"] == "Theme"
    r = svc.apply("Theme")
    assert "Playmats/Blue.png" in r["applied"]
    assert svc.packs()[0]["applied"] >= 1
    rem = svc.remove("Theme")
    assert rem["restored"] >= 1
    assert svc.packs() == []


def test_coverage_hook(svc, tmp_path):
    svc.add_source(str(_pack(tmp_path / "src")), name="Theme")   # couvre OP01-001
    svc.import_deck(text="1xOP01-001\n" + "\n".join(f"4xZZ{i:02d}-001" for i in range(12))
                    + "\n2xYY01-001", name="MonDeck")
    cov = svc.coverage("Theme")
    row = cov["decks"][0]
    assert row["deck"] == "MonDeck"
    assert row["covered"] == 1           # seul OP01-001 (=leader) est dans le pack
    assert row["total"] == 14


def test_import_deck_writes_sim_and_db(svc):
    r = svc.import_deck(text="1xOP01-060\n" + "\n".join(f"4xAA{i:02d}-001" for i in range(12))
                        + "\n2xBB01-001", name="D")
    assert r["leader"] == "OP01-060" and r["total"] == 50
    assert Path(r["path"]).exists()
    assert svc.decks()[0]["name"] == "D"


def test_decks_include_source_field(svc):
    svc.import_deck(text=_DECK50, name="D")
    assert svc.decks()[0]["source"] == "ui"


# ------------------------------------------------------------------ retrait de deck
def test_remove_deck_deletes_db_record_and_sim_file(svc):
    r = svc.import_deck(text=_DECK50, name="ARetirer")
    deck_id = svc.decks()[0]["id"]
    assert Path(r["path"]).exists()
    rem = svc.remove_deck(deck_id)
    assert rem == {"name": "ARetirer", "removed_file": True}
    assert svc.decks() == []
    assert not Path(r["path"]).exists()


def test_remove_deck_keeps_file_if_modified_since_import(svc):
    r = svc.import_deck(text=_DECK50, name="Modifié")
    deck_id = svc.decks()[0]["id"]
    Path(r["path"]).write_text("contenu retapé à la main, plus le deck importé\n")
    rem = svc.remove_deck(deck_id)
    assert rem == {"name": "Modifié", "removed_file": False}
    assert svc.decks() == []                 # tombstone en base malgré tout
    assert Path(r["path"]).exists()           # fichier laissé intact


def test_remove_deck_unknown_id_raises_keyerror(svc):
    with pytest.raises(KeyError):
        svc.remove_deck("inconnu")


# ------------------------------------------------------------------ P6 : pack de decks
_DECK50 = "1xOP01-060\n" + "\n".join(f"4xAA{i:02d}-001" for i in range(12)) + "\n2xBB01-001"


def test_import_deckpack_from_folder(svc, tmp_path):
    import json
    src = tmp_path / "pack"
    src.mkdir()
    (src / "deckpack.json").write_text(json.dumps({
        "name": "Meta OP16", "author": "Trecore", "decks": [
            {"name": "Aggro", "tags": ["meta"], "text": _DECK50},
            {"name": "Cassé", "text": "1xOP01-060\n4xAA01-001"},   # invalide
        ]}))
    r = svc.import_deckpack(str(src))
    assert r["name"] == "Meta OP16"
    assert [d["name"] for d in r["imported"]] == ["Aggro"]
    assert r["failed"][0]["name"] == "Cassé"
    # deck bien persisté en base
    assert "Aggro" in {d["name"] for d in svc.decks()}


def test_validate_deckpack_is_dry_run(svc, tmp_path):
    import json
    src = tmp_path / "pack"
    src.mkdir()
    (src / "deckpack.json").write_text(json.dumps({
        "name": "Contrôle", "schema_version": 999, "decks": [
            {"name": "Ok", "text": _DECK50},
            {"name": "Cassé", "text": "1xOP01-060"},   # invalide
        ]}))
    r = svc.validate_deckpack(str(src))
    assert r["valid"] is False
    assert [d["name"] for d in r["imported"]] == ["Ok"]
    assert r["failed"][0]["name"] == "Cassé"
    assert r["warnings"]                                   # version future signalée
    # dry-run : RIEN persisté
    assert "Ok" not in {d["name"] for d in svc.decks()}


# ------------------------------------------------------------------ sync jeu -> studio
def test_sync_from_sim_imports_new_deck_written_directly_in_game(svc):
    (svc.install.persistent / "FaitEnJeu.txt").write_text(_DECK50)
    r = svc.sync_from_sim()
    assert r == {"new": ["FaitEnJeu"], "updated": [], "orphaned": []}
    row = svc.decks()[0]
    assert row["name"] == "FaitEnJeu" and row["source"] == "sim"


def test_sync_from_sim_preserves_tags_on_update(svc):
    (svc.install.persistent / "FaitEnJeu.txt").write_text(_DECK50)
    svc.sync_from_sim()
    deck_id = svc.decks()[0]["id"]
    with svc._store() as store:
        store.put("decks", {**store.get("decks", deck_id), "tags": ["gardé"]})
    changed = ("1xOP02-001\n" + "\n".join(f"4xCC{i:02d}-001" for i in range(12))
              + "\n2xDD01-001")
    (svc.install.persistent / "FaitEnJeu.txt").write_text(changed)
    r = svc.sync_from_sim()
    assert r == {"new": [], "updated": ["FaitEnJeu"], "orphaned": []}
    row = svc.decks()[0]
    assert row["leader"] == "OP02-001" and row["tags"] == ["gardé"]


def test_sync_from_sim_reports_orphan_without_deleting(svc):
    (svc.install.persistent / "FaitEnJeu.txt").write_text(_DECK50)
    svc.sync_from_sim()
    (svc.install.persistent / "FaitEnJeu.txt").unlink()
    r = svc.sync_from_sim()
    assert r == {"new": [], "updated": [], "orphaned": ["FaitEnJeu"]}
    assert len(svc.decks()) == 1


def test_http_sync_from_sim(server, svc):
    (svc.install.persistent / "FaitEnJeu.txt").write_text(_DECK50)
    code, r = _post(server, "/api/decks/sync-from-sim", {})
    assert code == 200 and r["new"] == ["FaitEnJeu"]


# ------------------------------------------------------------------ génération de deckpack
def test_export_deckpack_round_trips_selected_decks(svc):
    from studio.decks.importer import parse_text
    svc.import_deck(text=_DECK50, name="Aggro", tags=["meta"])
    deck_id = svc.decks()[0]["id"]
    pack = svc.export_deckpack([deck_id], "Mon pack", author="Moi")
    assert pack["name"] == "Mon pack" and pack["author"] == "Moi"
    assert pack["schema_version"] == 1
    assert len(pack["decks"]) == 1
    d = pack["decks"][0]
    assert d["name"] == "Aggro" and d["tags"] == ["meta"]
    reparsed = parse_text(d["text"])
    assert reparsed.leader == "OP01-060" and reparsed.total == 50


def test_export_deckpack_unknown_id_raises_keyerror(svc):
    with pytest.raises(KeyError):
        svc.export_deckpack(["inconnu"], "P")


def test_http_export_deckpack(server, svc):
    svc.import_deck(text=_DECK50, name="Aggro")
    deck_id = svc.decks()[0]["id"]
    code, r = _post(server, "/api/deckpacks/export", {"ids": [deck_id], "name": "P"})
    assert code == 200 and r["decks"][0]["name"] == "Aggro"


def test_http_export_deckpack_unknown_id_is_404(server):
    req = urllib.request.Request(
        server + "/api/deckpacks/export",
        data=json.dumps({"ids": ["inconnu"], "name": "P"}).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_http_deckpack_is_job_based(server, svc, tmp_path):
    import json
    src = tmp_path / "dp"
    src.mkdir()
    (src / "deckpack.json").write_text(json.dumps(
        {"name": "P", "decks": [{"name": "Solo", "tags": ["x"], "text": _DECK50}]}))
    code, r = _post(server, "/api/deckpacks/add", {"source": str(src)})
    assert code == 202 and "job_id" in r
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "done"
    assert st["result"]["imported"][0]["name"] == "Solo"


def test_http_decks_remove(server, svc):
    code, r = _post(server, "/api/decks/import", {"text": _DECK50, "name": "AuHttp"})
    assert code == 200
    deck_id = svc.decks()[0]["id"]
    code, r = _post(server, f"/api/decks/{deck_id}/remove", {})
    assert code == 200 and r == {"name": "AuHttp", "removed_file": True}
    assert svc.decks() == []


def test_http_decks_remove_unknown_is_404(server):
    req = urllib.request.Request(server + "/api/decks/inconnu/remove",
                                 data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


# ------------------------------------------------------------------ serveur HTTP réel
@pytest.fixture()
def server(svc):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(svc))
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def _get(base, path):
    with urllib.request.urlopen(base + path) as r:
        return r.status, json.loads(r.read())


def _post(base, path, body, headers=None):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return r.status, json.loads(r.read())


def test_http_serves_index(server):
    with urllib.request.urlopen(server + "/") as r:
        assert r.status == 200 and b"OPTCGSim" in r.read()


# ------------------------------------------------------------------ P7 : config token (secret)
def test_config_token_never_returned_in_clear(server, svc, monkeypatch, tmp_path):
    from studio.config import Config
    monkeypatch.setattr(svc, "config", Config(state_dir=tmp_path / "cfg"))
    code, r = _get(server, "/api/config")
    assert code == 200 and r == {"github_token_set": False, "default_collection_source": None}
    code, r = _post(server, "/api/config", {"github_token": "ghp_secret"})
    assert r == {"github_token_set": True, "default_collection_source": None}  # jamais la valeur
    code, r = _get(server, "/api/config")
    assert r == {"github_token_set": True, "default_collection_source": None}
    assert "ghp_secret" not in json.dumps(r)


# ------------------------------------------------------------------ P12 : collection par défaut
def test_config_default_collection_source_returned_in_clear(server, svc, monkeypatch, tmp_path):
    from studio.config import Config
    monkeypatch.setattr(svc, "config", Config(state_dir=tmp_path / "cfg"))
    code, r = _post(server, "/api/config", {"default_collection_source": "/tmp/collection.json"})
    assert code == 200
    assert r == {"github_token_set": False, "default_collection_source": "/tmp/collection.json"}
    code, r = _get(server, "/api/config")
    assert r["default_collection_source"] == "/tmp/collection.json"


def test_config_default_collection_source_cleared_with_null(server, svc, monkeypatch, tmp_path):
    from studio.config import Config
    monkeypatch.setattr(svc, "config", Config(state_dir=tmp_path / "cfg"))
    _post(server, "/api/config", {"default_collection_source": "/tmp/collection.json"})
    code, r = _post(server, "/api/config", {"default_collection_source": None})
    assert code == 200 and r["default_collection_source"] is None


def test_config_token_and_default_collection_independent(server, svc, monkeypatch, tmp_path):
    from studio.config import Config
    monkeypatch.setattr(svc, "config", Config(state_dir=tmp_path / "cfg"))
    _post(server, "/api/config", {"github_token": "ghp_secret"})
    code, r = _post(server, "/api/config", {"default_collection_source": "/tmp/collection.json"})
    assert r == {"github_token_set": True, "default_collection_source": "/tmp/collection.json"}
    # régler l'un ne doit jamais effacer l'autre (clé absente du body = inchangé)
    code, r = _post(server, "/api/config", {"github_token": None})
    assert r == {"github_token_set": False, "default_collection_source": "/tmp/collection.json"}


# ------------------------------------------------------------------ P7 : preview
def test_preview_non_explorable_source(server, tmp_path):
    code, r = _post(server, "/api/packs/preview", {"source": str(tmp_path)})
    assert code == 200 and r["explorable"] is False


def test_preview_github_sizes(server, svc, monkeypatch):
    from studio.assets import sourcefetch
    remote = [sourcefetch.RemoteFile("Cards/OP01/OP01-001.png", 1000),   # leader
              sourcefetch.RemoteFile("Cards/OP01/OP01-016.png", 1000),   # perso
              sourcefetch.RemoteFile("Playmats/Blue.png", 5000)]
    monkeypatch.setattr(sourcefetch, "list_remote_files", lambda url, token=None: remote)
    code, r = _post(server, "/api/packs/preview", {"source": "https://github.com/o/r"})
    assert r["explorable"] is True and r["files"] == 3
    assert r["sizes"]["total"] == 7000
    assert r["sizes"]["cards"] == 2000       # les 2 cartes, pas le playmat
    assert r["sizes"]["leader"] == 1000      # le seul leader (clé par type)


# ------------------------------------------------------------------ P7 : add filtré (job)
def test_resolve_filter_only_types(svc):
    from studio.assets import cardmeta
    cats, cards = svc._resolve_filter(None, None, False, only_types=["event"])
    assert cats == {"cards"}
    assert cards == set(cardmeta.ids_of_type("event"))
    # combinaison event + leader = union
    cats2, cards2 = svc._resolve_filter(None, None, False, only_types=["event", "leader"])
    assert cards2 == set(cardmeta.ids_of_type("event")) | set(cardmeta.ids_of_type("leader"))


def test_add_with_leaders_only_filter(server, svc, tmp_path, monkeypatch):
    from studio.assets import sourcefetch
    remote = [sourcefetch.RemoteFile("Cards/OP01/OP01-001.png", 1),   # leader
              sourcefetch.RemoteFile("Cards/OP01/OP01-016.png", 1)]   # perso
    monkeypatch.setattr(sourcefetch, "list_remote_files", lambda url, token=None: remote)
    fetched = {}
    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        fetched["paths"] = list(paths)
        for p in paths:
            make_png(Path(dest) / p)
        return Path(dest)
    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)
    code, r = _post(server, "/api/packs/add",
                    {"source": "https://github.com/o/r", "name": "L", "leaders_only": True})
    assert code == 202
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "done"
    assert fetched["paths"] == ["Cards/OP01/OP01-001.png"]      # SEUL le leader téléchargé
    assert st["result"]["cards"] == ["OP01-001"]


def test_http_inventory(server):
    code, inv = _get(server, "/api/inventory")
    assert code == 200 and inv["os"] == "test"


# ------------------------------------------------------------------ P10-c : collections
_COL_MANIFEST = {
    "name": "FR classique + full-art",
    "packs": [
        {"url": "https://github.com/o/fr-classic", "label": "Cartes FR classiques",
         "variant_group": "cards"},
        {"url": "https://github.com/o/fr-fullart", "label": "Cartes FR alternatives",
         "variant_group": "cards"},
        {"url": "https://github.com/o/fr-trans", "label": "Traduction FR"},
    ],
}


def test_resolve_collection_from_local_file(svc, tmp_path):
    p = tmp_path / "collection.json"
    p.write_text(json.dumps(_COL_MANIFEST))
    r = svc.resolve_collection(str(p))
    assert r["name"] == "FR classique + full-art"
    assert len(r["packs"]) == 3
    assert r["packs"][0]["variant_group"] == "cards"
    assert r["warnings"] == []


def test_resolve_collection_missing_local_file_raises(svc, tmp_path):
    from studio.assets import collections
    with pytest.raises(collections.CollectionError, match="introuvable"):
        svc.resolve_collection(str(tmp_path / "nexiste-pas.json"))


def test_resolve_collection_invalid_json_raises(svc, tmp_path):
    from studio.assets import collections
    p = tmp_path / "collection.json"
    p.write_text("{ pas du json")
    with pytest.raises(collections.CollectionError, match="JSON invalide"):
        svc.resolve_collection(str(p))


def test_resolve_collection_fetches_url_with_github_token(svc, monkeypatch, tmp_path):
    """URL http(s) : téléchargée via urllib ; un token GitHub configuré est envoyé en en-tête
    Authorization (utile si le manifeste est hébergé sur un dépôt/gist privé)."""
    import io

    from studio.api import server as server_mod
    from studio.config import Config
    monkeypatch.setattr(svc, "config", Config(state_dir=tmp_path / "cfg"))
    svc.config.set_github_token("ghp_secret")
    seen = {}

    class FakeResp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    def fake_urlopen(req, timeout=30, context=None):
        seen["headers"] = dict(req.header_items())
        return FakeResp(json.dumps(_COL_MANIFEST).encode())
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake_urlopen)

    r = svc.resolve_collection("https://raw.githubusercontent.com/o/r/main/collection.json")
    assert r["name"] == "FR classique + full-art"
    assert seen["headers"].get("Authorization") == "Bearer ghp_secret"


def test_resolve_collection_http_error_is_readable(svc, monkeypatch, tmp_path):
    import urllib.error

    from studio.api import server as server_mod

    def fake_urlopen(req, timeout=30, context=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)
    monkeypatch.setattr(server_mod.urllib.request, "urlopen", fake_urlopen)

    from studio.assets import collections
    with pytest.raises(collections.CollectionError, match="HTTP 404"):
        svc.resolve_collection("https://example.com/collection.json")


def test_http_collections_resolve(server, tmp_path):
    p = tmp_path / "collection.json"
    p.write_text(json.dumps(_COL_MANIFEST))
    code, r = _post(server, "/api/collections/resolve", {"source": str(p)})
    assert code == 200
    assert r["name"] == "FR classique + full-art"
    assert {pk["label"] for pk in r["packs"]} == {
        "Cartes FR classiques", "Cartes FR alternatives", "Traduction FR"}


def test_http_collections_resolve_error_is_400(server, tmp_path):
    req = urllib.request.Request(
        server + "/api/collections/resolve",
        data=json.dumps({"source": str(tmp_path / "manquant.json")}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 400"
    except urllib.error.HTTPError as e:
        assert e.code == 400
        assert "introuvable" in json.loads(e.read())["error"]


def _wait_job(server, job_id, timeout=5.0):
    """Interroge /api/jobs/<id> jusqu'à ce qu'il ne soit plus 'running' (ou timeout)."""
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, st = _get(server, f"/api/jobs/{job_id}")
        assert code == 200
        if st["status"] != "running":
            return st
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} toujours 'running' après {timeout}s")


def test_http_add_is_job_based_and_survives_polling(server, svc, tmp_path):
    """L'ajout renvoie IMMÉDIATEMENT un job_id (202) — le téléchargement/normalisation tourne
    en tâche de fond, indépendant de la requête HTTP d'origine."""
    code, r = _post(server, "/api/packs/add", {"source": str(_pack(tmp_path / "s")),
                                               "name": "ViaSource"})
    assert code == 202 and "job_id" in r
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "done"
    assert st["result"]["name"] == "ViaSource"
    assert {p["name"] for p in svc.packs()} == {"ViaSource"}


def test_http_upload_is_job_based(server, svc, tmp_path):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        p = _pack(tmp_path / "z")
        for f in p.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(p))
    code, r = _post(server, "/api/packs/upload", buf.getvalue(),
                    headers={"X-Filename": "MonPack.zip"})
    assert code == 202 and "job_id" in r
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "done" and st["result"]["name"] == "MonPack"
    assert "MonPack" in {p["name"] for p in svc.packs()}


def test_http_apply_is_job_based_with_progress(server, svc, tmp_path):
    svc.add_source(str(_pack(tmp_path / "s")), name="ToApply")
    code, r = _post(server, "/api/packs/ToApply/apply", {})
    assert code == 202 and "job_id" in r
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "done"
    assert "Playmats/Blue.png" in st["result"]["applied"]
    # la progression a bien été rapportée à un moment (phase="apply")
    assert st["phase"] == "apply" and st["total"] > 0


def test_http_job_error_surfaces_via_status(server, tmp_path):
    code, r = _post(server, "/api/packs/add", {"source": str(tmp_path / "nexiste-pas")})
    assert code == 202
    st = _wait_job(server, r["job_id"])
    assert st["status"] == "error"
    assert "non exploitable" in st["error"]


def test_http_unknown_job_is_404(server):
    req = urllib.request.Request(server + "/api/jobs/inconnu")
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404


def test_http_error_is_json(server):
    req = urllib.request.Request(server + "/api/packs/inconnu/coverage")
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404 and "error" in json.loads(e.read())
