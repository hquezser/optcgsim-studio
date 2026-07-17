"""Tests du gestionnaire d'assets — sur une FAUSSE installation en tmp (jamais la vraie)."""

import struct
import zlib
from pathlib import Path

import pytest

from studio.assets.manager import AssetError, AssetManager, image_info
from studio.gamepaths import GameInstall


# ------------------------------------------------------------------ fabrique d'images valides
def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    """PNG 1-pixel-data minimal mais structurellement valide (magic + IHDR corrects)."""
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data)))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00" + b"\x00\x00\x00" * 1)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", raw) + chunk(b"IEND", b""))
    return path


def make_jpeg(path: Path, w: int = 480, h: int = 669) -> Path:
    """JPEG minimal : SOI + SOF0 portant les dimensions + EOI."""
    sof = struct.pack(">BBHHBHHB", 0xFF, 0xC0, 11, h, 8, w & 0xFFFF, 0, 1)
    # struct ci-dessus n'aligne pas exactement SOF0 : construit à la main.
    sof = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")
    return path


@pytest.fixture()
def fake_install(tmp_path) -> GameInstall:
    sa = tmp_path / "app" / "StreamingAssets"
    persistent = tmp_path / "persistent"
    (sa / "Cards" / "OP01").mkdir(parents=True)
    (sa / "Playmats").mkdir()
    (sa / "CardBacks").mkdir()
    (persistent / "1.41b" / "Cards").mkdir(parents=True)
    make_png(sa / "Cards" / "OP01" / "OP01-001.png")
    make_jpeg(sa / "Cards" / "OP01" / "OP01-001_small.jpg", 120, 168)
    make_jpeg(persistent / "1.41b" / "Cards" / "OP17-001.jpg")
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    make_png(sa / "CardBacks" / "CardBackRegular.png")
    make_png(sa / "CardBacks" / "CardBackDon.png")
    make_jpeg(sa / "background.jpg", 1920, 1080)
    (sa / "TRANSLATION.txt").write_text(
        "Button.Single=Solo v Self\nButton.Multi=Multiplayer\nButton.Back=Back\n")
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=persistent, os_name="test", verified=True)


@pytest.fixture()
def mgr(fake_install, tmp_path) -> AssetManager:
    return AssetManager(fake_install, state_dir=tmp_path / "state")


# ------------------------------------------------------------------ validation d'images
def test_image_info_reads_real_headers(tmp_path):
    assert image_info(make_png(tmp_path / "a.png", 480, 671)) == ("png", 480, 671)
    assert image_info(make_jpeg(tmp_path / "a.jpg", 480, 669)) == ("jpeg", 480, 669)


def test_garbage_file_is_rejected(mgr, tmp_path):
    bad = tmp_path / "evil.png"
    bad.write_bytes(b"#!/bin/sh\nrm -rf /\n")
    with pytest.raises(AssetError, match="non reconnu"):
        mgr.apply_playmat("Blue", bad)


def test_wrong_ratio_card_rejected(mgr, tmp_path):
    square = make_png(tmp_path / "sq.png", 500, 500)
    with pytest.raises(AssetError, match="Ratio"):
        mgr.apply_card("OP01-001", square)


# ------------------------------------------------------------------ swap + backup + restore
def test_card_swap_backs_up_and_replaces_matching_formats(mgr, fake_install, tmp_path):
    new = make_png(tmp_path / "custom.png", 960, 1342)   # proxy HD, bon ratio
    done = mgr.apply_card("OP01-001", new)
    target = fake_install.cards_dir / "OP01" / "OP01-001.png"
    assert done == [target]                       # le _small.jpg (jpeg) n'est pas touché
    assert target.read_bytes() == new.read_bytes()
    st = mgr.status()
    assert len(st) == 1 and st[0]["state"] == "active"


def test_versioned_cache_jpeg_swap(mgr, fake_install, tmp_path):
    new = make_jpeg(tmp_path / "c.jpg", 480, 669)
    done = mgr.apply_card("OP17-001", new)
    assert done == [fake_install.persistent / "1.41b" / "Cards" / "OP17-001.jpg"]


def test_restore_returns_pristine_original(mgr, fake_install, tmp_path):
    target = fake_install.playmats_dir / "Blue.png"
    original = target.read_bytes()
    mgr.apply_playmat("Blue", make_png(tmp_path / "m1.png", 1920, 1080))
    mgr.apply_playmat("Blue", make_png(tmp_path / "m2.png", 2560, 1440))  # 2e swap
    assert target.read_bytes() != original
    mgr.restore(target)
    assert target.read_bytes() == original        # PRISTINE, pas m1
    assert mgr.status() == []


def test_restore_all_roundtrip(mgr, fake_install, tmp_path):
    originals = {}
    for rel in ("Playmats/Blue.png", "CardBacks/CardBackRegular.png"):
        originals[rel] = (fake_install.streaming_assets / rel).read_bytes()
    mgr.apply_playmat("Blue", make_png(tmp_path / "m.png"))
    mgr.apply_cardback(make_png(tmp_path / "cb.png"))
    assert mgr.restore_all() == 2
    for rel, data in originals.items():
        assert (fake_install.streaming_assets / rel).read_bytes() == data


def test_status_detects_sim_update_overwrite(mgr, fake_install, tmp_path):
    mgr.apply_playmat("Blue", make_png(tmp_path / "m.png"))
    # une « mise à jour du sim » écrase le fichier
    make_png(fake_install.playmats_dir / "Blue.png", 1000, 700)
    assert mgr.status()[0]["state"] == "overwritten"


# ------------------------------------------------------------------ garde-fous
def test_target_outside_allowed_roots_rejected(mgr, tmp_path):
    outside = make_png(tmp_path / "x.png")
    with pytest.raises(AssetError, match="autorisées"):
        mgr._swap(tmp_path / "somewhere.png", outside, "t")


def test_symlink_source_rejected(mgr, fake_install, tmp_path):
    real = make_png(tmp_path / "real.png")
    link = tmp_path / "link.png"
    link.symlink_to(real)
    with pytest.raises(AssetError, match="Symlink"):
        mgr.apply_playmat("Blue", link)


def test_unknown_card_rejected(mgr, tmp_path):
    with pytest.raises(AssetError, match="inconnue"):
        mgr.apply_card("ZZ99-999", make_png(tmp_path / "c.png"))


# ------------------------------------------------------------------ traduction (fusion)
def test_translation_merge_keeps_unknown_keys_official(mgr, fake_install, tmp_path):
    ov = tmp_path / "fr.txt"
    ov.write_text("Button.Single=Solo contre soi\nButton.Ghost=N'existe pas\n")
    mgr.apply_translation(ov)
    txt = fake_install.translation_file.read_text()
    assert "Button.Single=Solo contre soi" in txt      # clé traduite
    assert "Button.Multi=Multiplayer" in txt           # clé non couverte : officielle
    assert "Ghost" not in txt                          # clé inconnue : ignorée
    mgr.restore(fake_install.translation_file)
    assert "Solo v Self" in fake_install.translation_file.read_text()


# ------------------------------------------------------------------ pack complet
def test_apply_pack_drag_and_drop_layout(mgr, fake_install, tmp_path):
    pack = tmp_path / "MonPack"
    (pack / "cards").mkdir(parents=True)
    (pack / "playmats").mkdir()
    make_png(pack / "cards" / "OP01-001.png", 960, 1342)
    make_png(pack / "playmats" / "Blue.png", 1920, 1080)
    make_png(pack / "cardback.png")
    (pack / "translation.txt").write_text("Button.Back=Retour\n")
    counts = mgr.apply_pack(pack)
    assert counts == {"cards": 1, "playmats": 1, "cardbacks": 1,
                      "backgrounds": 0, "translation": 1}
    assert "Button.Back=Retour" in fake_install.translation_file.read_text()
    assert mgr.restore_all() == 4
