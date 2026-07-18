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
                         lib_dir=tmp_path / "lib")


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


def test_http_inventory(server):
    code, inv = _get(server, "/api/inventory")
    assert code == 200 and inv["os"] == "test"


def test_http_add_and_upload(server, svc, tmp_path):
    import io
    import zipfile
    # add par source (dossier)
    code, r = _post(server, "/api/packs/add", {"source": str(_pack(tmp_path / "s")),
                                               "name": "ViaSource"})
    assert code == 200 and r["name"] == "ViaSource"
    # upload d'un zip (drag & drop)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        p = _pack(tmp_path / "z")
        for f in p.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(p))
    code, r = _post(server, "/api/packs/upload", buf.getvalue(),
                    headers={"X-Filename": "MonPack.zip"})
    assert code == 200 and r["name"] == "MonPack"
    names = {p["name"] for p in svc.packs()}
    assert {"ViaSource", "MonPack"} <= names


def test_http_error_is_json(server):
    req = urllib.request.Request(server + "/api/packs/inconnu/coverage")
    try:
        urllib.request.urlopen(req)
        assert False, "aurait dû lever 404"
    except urllib.error.HTTPError as e:
        assert e.code == 404 and "error" in json.loads(e.read())
