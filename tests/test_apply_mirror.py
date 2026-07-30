"""Tests de la primitive apply_mirror (P0) — packs calqués sur StreamingAssets (Themer & co).

Fausse installation en tmp — jamais la vraie.
"""

import struct
import zlib
from pathlib import Path

import pytest

from studio.assets.manager import AssetManager
from studio.gamepaths import GameInstall


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(typ: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data)))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
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
    (sa / "Cards" / "Don").mkdir(parents=True)
    (sa / "Playmats").mkdir()
    (sa / "CardBacks").mkdir()
    make_png(sa / "Cards" / "OP01" / "OP01-001.png")
    make_png(sa / "Cards" / "Don" / "Don.png")            # id hors gabarit CARD_ID
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    make_png(sa / "CardBacks" / "CardBackRegular.png")
    make_jpeg(sa / "background.jpg")
    (sa / "TRANSLATION.txt").write_text("Button.Back=Back\n")
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)


@pytest.fixture()
def mgr(install, tmp_path) -> AssetManager:
    return AssetManager(install, state_dir=tmp_path / "state")


def _themer_pack(root: Path) -> Path:
    """Pack au format Themer : miroir de StreamingAssets."""
    make_png(root / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    make_png(root / "Cards" / "Don" / "Don.png")
    make_png(root / "Playmats" / "Blue.png", 2560, 1440)
    make_png(root / "CardBacks" / "CardBackRegular.png")
    make_jpeg(root / "background.jpg", 3840, 2160)
    return root


def test_mirror_replaces_all_categories_including_don(mgr, install, tmp_path):
    rep = mgr.apply_mirror(_themer_pack(tmp_path / "pack"))
    applied = set(rep["applied"])
    assert applied == {
        "Cards/OP01/OP01-001.png", "Cards/Don/Don.png",
        "Playmats/Blue.png", "CardBacks/CardBackRegular.png", "background.jpg"}
    # le DON (id hors gabarit) est bien passé par le chemin miroir
    assert install.streaming_assets.joinpath("Cards/Don/Don.png").read_bytes() \
        == (tmp_path / "pack" / "Cards" / "Don" / "Don.png").read_bytes()
    assert rep["ignored"] == []


def test_mirror_never_creates_unknown_files(mgr, install, tmp_path):
    pack = _themer_pack(tmp_path / "pack")
    make_png(pack / "Cards" / "OP99" / "OP99-999.png")     # set absent de l'install
    make_png(pack / "Fanart" / "wallpaper.png")            # dossier inconnu
    rep = mgr.apply_mirror(pack)
    ignored = {i["path"] for i in rep["ignored"]}
    assert "Cards/OP99/OP99-999.png" in ignored
    assert "Fanart/wallpaper.png" in ignored
    # rien n'a été créé hors des cibles existantes
    assert not install.streaming_assets.joinpath("Cards/OP99").exists()
    assert not install.streaming_assets.joinpath("Fanart").exists()


def test_mirror_skips_translation_txt(mgr, tmp_path):
    pack = _themer_pack(tmp_path / "pack")
    (pack / "TRANSLATION.txt").write_text("Button.Back=Retour\n")
    rep = mgr.apply_mirror(pack)
    assert rep["skipped_txt"] == ["TRANSLATION.txt"]       # -> passe par apply_translation


def test_mirror_format_mismatch_ignored(mgr, tmp_path):
    pack = tmp_path / "pack"
    make_jpeg(pack / "Cards" / "OP01" / "OP01-001.png")    # cible PNG, source JPEG déguisée
    rep = mgr.apply_mirror(pack)
    assert rep["applied"] == []
    assert any("format" in i["reason"] for i in rep["ignored"])


def test_mirror_rejects_garbage_image(mgr, tmp_path):
    pack = tmp_path / "pack"
    (pack / "Playmats").mkdir(parents=True)
    (pack / "Playmats" / "Blue.png").write_bytes(b"not an image")
    rep = mgr.apply_mirror(pack)
    assert rep["applied"] == []
    assert rep["ignored"][0]["path"] == "Playmats/Blue.png"


def test_mirror_unwraps_single_enclosing_folder(mgr, tmp_path):
    # zip décompressé avec un dossier englobant « MyTheme_v2/ »
    wrapped = tmp_path / "download" / "MyTheme_v2"
    _themer_pack(wrapped)
    rep = mgr.apply_mirror(tmp_path / "download")
    assert "Playmats/Blue.png" in rep["applied"]
    assert rep["root"].endswith("MyTheme_v2")


def test_mirror_is_reversible(mgr, install, tmp_path):
    original = install.streaming_assets.joinpath("Playmats/Blue.png").read_bytes()
    mgr.apply_mirror(_themer_pack(tmp_path / "pack"))
    assert install.streaming_assets.joinpath("Playmats/Blue.png").read_bytes() != original
    rep = mgr.restore_all()
    assert rep == {"restored": 5, "failed": []}
    assert install.streaming_assets.joinpath("Playmats/Blue.png").read_bytes() == original


# ------------------------------------------------- P13.4 : une prévisualisation n'écrit JAMAIS
def test_apply_mirror_dry_run_writes_nothing(mgr, install, tmp_path):
    """Invariant du bouton « Prévisualiser » de l'UI : annoncer sans jamais toucher au jeu.

    Verrouille le `continue` de la branche `dry_run` : le déplacer après le `_swap` (ou le
    perdre) écraserait les fichiers du jeu pendant ce que l'utilisateur croit être une simple
    analyse. Aucun autre test n'exerce `dry_run=True`.
    """
    sa = install.streaming_assets
    before = {p: p.read_bytes() for p in sa.rglob("*") if p.is_file()}

    rep = mgr.apply_mirror(_themer_pack(tmp_path / "pack"), dry_run=True)

    assert rep["applied"]                            # le rapport annonce bien le travail
    assert {p: p.read_bytes() for p in sa.rglob("*") if p.is_file()} == before
    assert mgr.status() == []                        # aucun swap enregistré, rien à restaurer
    assert not list(mgr.backup_dir.glob("*")) if mgr.backup_dir.exists() else True
