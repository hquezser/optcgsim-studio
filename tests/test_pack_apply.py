"""Tests P2 : application de packs (filtre catégorie, collisions, restauration par pack)."""

import struct
import zlib
from pathlib import Path

import pytest

from studio.assets.manager import AssetManager
from studio.gamepaths import GameInstall


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))
    return path


def make_jpeg(path: Path, w: int = 1920, h: int = 1080) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sof = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")
    return path


@pytest.fixture()
def install(tmp_path) -> GameInstall:
    sa = tmp_path / "app" / "StreamingAssets"
    (sa / "Cards" / "OP01").mkdir(parents=True)
    (sa / "Playmats").mkdir(parents=True)
    make_png(sa / "Cards" / "OP01" / "OP01-001.png")
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    make_jpeg(sa / "background.jpg")
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)


@pytest.fixture()
def mgr(install, tmp_path) -> AssetManager:
    return AssetManager(install, state_dir=tmp_path / "state")


def _pack(root: Path) -> Path:
    make_png(root / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    make_png(root / "Playmats" / "Blue.png", 2560, 1440)
    make_jpeg(root / "background.jpg", 3840, 2160)
    return root


# ------------------------------------------------------------------ filtre --only
def test_only_filters_categories(mgr, tmp_path):
    rep = mgr.apply_mirror(_pack(tmp_path / "p"), origin="pack:A", only={"cards"})
    assert rep["applied"] == ["Cards/OP01/OP01-001.png"]
    assert set(rep["filtered"]) == {"Playmats/Blue.png", "background.jpg"}


def test_mirror_category():
    assert AssetManager.mirror_category("Cards/OP01/OP01-001.png") == "cards"
    assert AssetManager.mirror_category("Playmats/Blue.png") == "playmats"
    assert AssetManager.mirror_category("background.jpg") == "backgrounds"


# ------------------------------------------------------------------ collisions
def test_collision_last_pack_wins_backup_stays_original(mgr, install, tmp_path):
    target = install.streaming_assets / "Playmats" / "Blue.png"
    original = target.read_bytes()

    packA = tmp_path / "A"
    make_png(packA / "Playmats" / "Blue.png", 2000, 1125)
    mgr.apply_mirror(packA, origin="pack:A")
    a_bytes = target.read_bytes()
    assert a_bytes != original

    packB = tmp_path / "B"
    make_png(packB / "Playmats" / "Blue.png", 2560, 1440)
    rep = mgr.apply_mirror(packB, origin="pack:B")
    assert any(c["path"] == "Playmats/Blue.png" and c["previous"] == "pack:A"
               for c in rep["collisions"])
    assert target.read_bytes() != a_bytes                 # B a pris le dessus

    # restaurer B ramène à l'ORIGINAL (pas à la version de A) : backup pristine
    mgr.restore_source("pack:B")
    assert target.read_bytes() == original


# ------------------------------------------------------------------ restore_source
def test_restore_source_only_touches_that_pack(mgr, install, tmp_path):
    blue = install.streaming_assets / "Playmats" / "Blue.png"
    card = install.streaming_assets / "Cards" / "OP01" / "OP01-001.png"
    blue0, card0 = blue.read_bytes(), card.read_bytes()

    packMat = tmp_path / "mats"
    make_png(packMat / "Playmats" / "Blue.png", 2560, 1440)
    mgr.apply_mirror(packMat, origin="pack:mats")

    packCard = tmp_path / "cards"
    make_png(packCard / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    mgr.apply_mirror(packCard, origin="pack:cards")

    n = mgr.restore_source("pack:mats")
    assert n == 1
    assert blue.read_bytes() == blue0                      # mats restauré
    assert card.read_bytes() != card0                      # cards intact (toujours appliqué)
    assert [s["source"] for s in mgr.status()] == ["pack:cards"]


def test_restore_source_skips_target_retaken_by_other_pack(mgr, install, tmp_path):
    blue = install.streaming_assets / "Playmats" / "Blue.png"
    original = blue.read_bytes()
    a = tmp_path / "A"; make_png(a / "Playmats" / "Blue.png", 2000, 1125)
    b = tmp_path / "B"; make_png(b / "Playmats" / "Blue.png", 2560, 1440)
    mgr.apply_mirror(a, origin="pack:A")
    mgr.apply_mirror(b, origin="pack:B")                   # B retient Blue.png maintenant
    # retirer A ne doit PAS toucher Blue.png (tenu par B)
    n = mgr.restore_source("pack:A")
    assert n == 0
    assert blue.read_bytes() != original                   # toujours la version de B
    assert mgr.status()[0]["source"] == "pack:B"
