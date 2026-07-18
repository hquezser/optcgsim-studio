"""Tests du normaliseur packlib — les 3 layouts réels reproduits en synthétique."""

import struct
import zipfile
import zlib
from pathlib import Path

import pytest

from studio.assets import packlib
from studio.gamepaths import GameInstall


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00")) + chunk(b"IEND", b""))
    return path


def make_jpeg(path: Path, w: int = 120, h: int = 168) -> Path:
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
    make_png(sa / "Playmats" / "RedBlack.png", 1920, 1080)
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)


@pytest.fixture()
def lib(tmp_path) -> Path:
    return tmp_path / "lib"


# ------------------------------------------------------------------ layout Themer (miroir)
def test_themer_mirror_layout_preserved(install, lib, tmp_path):
    src = tmp_path / "themer"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    make_png(src / "Cards" / "Don" / "Don.png")
    make_png(src / "Playmats" / "Blue.png", 2560, 1440)
    make_png(src / "CardBacks" / "CardBackRegular.png")
    (src / "install_guide.txt").write_text("copy into StreamingAssets\n")   # pas Clé=Valeur
    pack, rep = packlib.normalize(src, install, "Themer", "themer.zip", lib)

    assert (pack / "Cards" / "OP01" / "OP01-001.png").exists()
    assert (pack / "Cards" / "Don" / "Don.png").exists()      # id hors gabarit, via miroir
    assert (pack / "Playmats" / "Blue.png").exists()
    assert "OP01-001" in rep.cards
    assert "Blue" in rep.playmats
    # présents dans l'install fixture : OP01-001 + Blue (Don et CardBacks absents)
    assert rep.present_in_install == 2


# ------------------------------------------------------------------ layout Dropbox (Cards/<SET>)
def test_dropbox_cards_with_small(install, lib, tmp_path):
    src = tmp_path / "dropbox"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    make_jpeg(src / "Cards" / "OP01" / "OP01-001_small.jpg")
    pack, rep = packlib.normalize(src, install, "AltJon", "dropbox", lib)
    assert (pack / "Cards" / "OP01" / "OP01-001.png").exists()
    assert (pack / "Cards" / "OP01" / "OP01-001_small.jpg").exists()   # _small préservé
    assert rep.cards == ["OP01-001"]


# ------------------------------------------------------------------ layout repo FR (_OVERRIDE)
def test_fr_repo_override_and_translation(install, lib, tmp_path):
    src = tmp_path / "fr"
    make_png(src / "FR_classique" / "OP01" / "OP01-001_OVERRIDE.png", 480, 671)
    make_png(src / "FR_full_art" / "OP01" / "OP01-003_OVERRIDE.png", 480, 671)
    (src / "TRANSLATION.txt").write_text(
        "Button.Single=Solo\nButton.Multi=Multi\nButton.Back=Retour\n"
        "Button.Start=Commencer\nButton.Save=Sauver\nButton.Load=Charger\n")
    pack, rep = packlib.normalize(src, install, "FR", "github", lib)

    # _OVERRIDE retiré, remappé sous Cards/<SET>/
    assert (pack / "Cards" / "OP01" / "OP01-001.png").exists()
    assert (pack / "TRANSLATION.txt").exists()
    assert rep.translation is True
    assert "OP01-001" in rep.cards
    # OP01-001 (classique) et OP01-003 (full art) ont des ids différents -> pas de collision
    assert "OP01-003" in rep.cards


def test_variant_collision_reported(install, lib, tmp_path):
    src = tmp_path / "v"
    make_png(src / "OP01-001.png")
    make_png(src / "OP01-001_alt.png")          # même id après strip -> variante
    pack, rep = packlib.normalize(src, install, "V", "x", lib)
    assert rep.cards == ["OP01-001"]
    assert len(rep.variants) == 1
    assert rep.variants[0]["target"] == "Cards/OP01/OP01-001.png"


def test_unclassified_reported_with_reason(install, lib, tmp_path):
    src = tmp_path / "u"
    make_png(src / "random_fanart.png")         # ni id, ni asset spécial
    make_png(src / "Extra Alts" / "cool_wallpaper.png")
    pack, rep = packlib.normalize(src, install, "U", "x", lib)
    paths = {u["path"] for u in rep.unclassified}
    assert "random_fanart.png" in paths
    assert all(u["reason"] for u in rep.unclassified)   # toujours une raison


def test_manifest_written(install, lib, tmp_path):
    src = tmp_path / "m"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    pack, rep = packlib.normalize(src, install, "M", "src", lib)
    import json
    manifest = json.loads((pack / "manifest.json").read_text())
    assert manifest["name"] == "M" and manifest["cards"] == ["OP01-001"]


# ------------------------------------------------------------------ ingestion (zip / sécurité)
def test_ingest_local_zip(install, lib, tmp_path):
    src = tmp_path / "content"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "Cards" / "OP01" / "OP01-001.png", "Cards/OP01/OP01-001.png")
    out = packlib.ingest(zpath, tmp_path / "work")
    assert (out / "Cards" / "OP01" / "OP01-001.png").exists()


def test_zip_slip_rejected(tmp_path):
    zpath = tmp_path / "evil.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("../../escape.png", b"x")
    with pytest.raises(packlib.PackError, match="zip-slip"):
        packlib.ingest(zpath, tmp_path / "work")


def test_dropbox_style_root_slash_entry_not_rejected(tmp_path):
    """Régression : les exports de dossier Dropbox incluent une entrée '/' en tête
    (marqueur du dossier racine, sans contenu). `dest / '/'` vaut '/' en pathlib (opérande
    absolu = base ignorée) -> ancien faux positif de zip-slip sur une archive légitime."""
    zpath = tmp_path / "dropbox_export.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("/", "")                                    # entrée racine Dropbox
        zf.writestr("Cards/OP01/OP01-001.png", b"fake-png-bytes")
    out = packlib.ingest(zpath, tmp_path / "work")
    assert (out / "Cards" / "OP01" / "OP01-001.png").read_bytes() == b"fake-png-bytes"


def test_url_resolution():
    assert packlib._resolve_url("https://github.com/Sparklight-TL/OPTCGSim_FR").endswith(
        "/OPTCGSim_FR/zip/refs/heads/main")
    assert packlib._resolve_url(
        "https://github.com/o/r/tree/dev").endswith("/r/zip/refs/heads/dev")
    assert "dl=1" in packlib._resolve_url("https://www.dropbox.com/scl/fo/x?rlkey=y&dl=0")


def test_add_pack_unwraps_github_single_folder(install, lib, tmp_path):
    # zip GitHub : tout est sous « repo-main/ »
    wrapped = tmp_path / "src" / "OPTCGSim_FR-main"
    make_png(wrapped / "FR_classique" / "OP01" / "OP01-001_OVERRIDE.png")
    (wrapped / "TRANSLATION.txt").write_text("\n".join(f"K{i}=v" for i in range(6)))
    pack, rep = packlib.add_pack(tmp_path / "src", install, name="FR", lib_dir=lib,
                                 work_dir=tmp_path / "src")
    assert "OP01-001" in rep.cards and rep.translation
