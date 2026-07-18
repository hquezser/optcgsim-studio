"""Tests de l'import sélectif (P7-d) : filtres only_categories / only_cards."""

import struct
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
                     + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b""))
    return path


@pytest.fixture()
def install(tmp_path):
    sa = tmp_path / "app" / "StreamingAssets"
    (sa / "Playmats").mkdir(parents=True)
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "p", os_name="test", verified=True)


# ------------------------------------------------------------------ classify_rel / keep_rel
def test_classify_rel_categories():
    assert packlib.classify_rel("Cards/OP01/OP01-001.png") == ("cards", "OP01-001")
    assert packlib.classify_rel("FR_classique/OP01/OP01-003_OVERRIDE.png") == ("cards", "OP01-003")
    assert packlib.classify_rel("Cards/OP01/OP01-001_small.jpg") == ("cards", "OP01-001")
    assert packlib.classify_rel("Playmats/Blue.png") == ("playmats", None)
    assert packlib.classify_rel("CardBacks/CardBackRegular.png") == ("cardbacks", None)
    assert packlib.classify_rel("background.jpg") == ("backgrounds", None)
    assert packlib.classify_rel("TRANSLATION.txt") == ("translation", None)
    assert packlib.classify_rel("random.png") == ("other", None)


def test_keep_rel_only_categories():
    assert packlib.keep_rel("Cards/OP01/OP01-001.png", {"cards"}, None)
    assert not packlib.keep_rel("Playmats/Blue.png", {"cards"}, None)


def test_keep_rel_only_cards():
    only = {"OP01-001"}
    assert packlib.keep_rel("Cards/OP01/OP01-001.png", None, only)
    assert not packlib.keep_rel("Cards/OP01/OP01-002.png", None, only)
    # un non-carte passe only_cards (le filtre carte ne le concerne pas)
    assert packlib.keep_rel("Playmats/Blue.png", None, only)


def test_keep_rel_leaders_only_composition():
    # "leaders alternatifs uniquement" = cards + ids leaders
    leaders = {"OP01-001"}   # OP01-001 est un leader (cf. cardmeta)
    assert packlib.keep_rel("Cards/OP01/OP01-001.png", {"cards"}, leaders)
    assert not packlib.keep_rel("Cards/OP01/OP01-016.png", {"cards"}, leaders)  # perso
    assert not packlib.keep_rel("Playmats/Blue.png", {"cards"}, leaders)        # pas une carte


# ------------------------------------------------------------------ normalize avec filtre (disque)
def _src(root: Path):
    make_png(root / "Cards" / "OP01" / "OP01-001.png")     # leader
    make_png(root / "Cards" / "OP01" / "OP01-016.png")     # perso
    make_png(root / "Playmats" / "Blue.png", 1920, 1080)
    return root


def test_normalize_only_cards_skips_others_on_disk(install, tmp_path):
    pack, rep = packlib.normalize(_src(tmp_path / "s"), install, "P", "x",
                                  tmp_path / "lib", only_categories={"cards"})
    assert (pack / "Cards" / "OP01" / "OP01-001.png").exists()
    assert not (pack / "Playmats" / "Blue.png").exists()   # filtré : pas copié
    assert "Playmats/Blue.png" in rep.filtered
    assert set(rep.cards) == {"OP01-001", "OP01-016"}


def test_normalize_leaders_only(install, tmp_path):
    from studio.assets import cardmeta
    leaders = set(cardmeta.leader_ids())
    pack, rep = packlib.normalize(_src(tmp_path / "s"), install, "P", "x",
                                  tmp_path / "lib",
                                  only_categories={"cards"}, only_cards=leaders)
    assert rep.cards == ["OP01-001"]                        # seul le leader
    assert (pack / "Cards" / "OP01" / "OP01-001.png").exists()
    assert not (pack / "Cards" / "OP01" / "OP01-016.png").exists()
    assert "Cards/OP01/OP01-016.png" in rep.filtered


def test_normalize_no_filter_keeps_everything(install, tmp_path):
    pack, rep = packlib.normalize(_src(tmp_path / "s"), install, "P", "x", tmp_path / "lib")
    assert rep.filtered == []
    assert (pack / "Playmats" / "Blue.png").exists()


# ------------------------------------------------------------------ add_pack fetch sélectif (mocké)
def test_add_pack_uses_selective_fetch_when_available(install, tmp_path, monkeypatch):
    from studio.assets import sourcefetch
    # simule une source GitHub explorable : 1 leader + 1 perso + 1 playmat
    remote = [sourcefetch.RemoteFile("Cards/OP01/OP01-001.png", 100),
              sourcefetch.RemoteFile("Cards/OP01/OP01-016.png", 100),
              sourcefetch.RemoteFile("Playmats/Blue.png", 100)]
    monkeypatch.setattr(sourcefetch, "list_remote_files", lambda url, token=None: remote)
    fetched = {}
    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        fetched["paths"] = paths
        for p in paths:
            make_png(Path(dest) / p, 1920, 1080) if "Playmats" in p else make_png(Path(dest) / p)
        return Path(dest)
    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)

    pack, rep = packlib.add_pack("https://github.com/o/r", install, name="Sel",
                                 lib_dir=tmp_path / "lib", work_dir=tmp_path / "w",
                                 only_categories={"cards"}, only_cards={"OP01-001"})
    # seul le leader a été TÉLÉCHARGÉ (pas juste filtré au disque)
    assert fetched["paths"] == ["Cards/OP01/OP01-001.png"]
    assert rep.cards == ["OP01-001"]
