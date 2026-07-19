"""Tests du constructeur de dépôts d'images (repobuild) — routage par famille + cartes/type,
génération hors-ligne (ingest factice), collisions, non-classés, et parsing/ingest Drive."""

import json
import struct
import zlib
from pathlib import Path

import pytest

from studio.assets import packlib, repobuild


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(b"\x00")) + chunk(b"IEND", b""))
    return path


# ------------------------------------------------------------------ route (pur)
def test_route_cards_by_type():
    # OP01-001 est un Leader, OP01-016 un Character (cf. cardmeta)
    assert repobuild.route("cards", "OP01-001", "OP01-001.png", "alt", True) == (
        "cards-alt", "Leaders/Cards/OP01/OP01-001.png")
    assert repobuild.route("cards", "OP01-016", "OP01-016_alt.png", "alt", True) == (
        "cards-alt", "Characters/Cards/OP01/OP01-016.png")   # nom canonicalisé


def test_route_cards_translated_family_and_txt():
    assert repobuild.route("cards", "OP01-001", "OP01-001_OVERRIDE.png", "translated", True)[0] \
        == "translations"
    assert repobuild.route("translation", None, "TRANSLATION.txt", "alt", True) == (
        "translations", "TRANSLATION.txt")


def test_route_fixed_families():
    assert repobuild.route("don", None, "Don.png", "alt", True) == (
        "cardbacks-don", "Cards/Don/Don.png")
    assert repobuild.route("cardbacks", None, "CardBackRegular.png", "alt", True) == (
        "cardbacks-don", "CardBacks/CardBackRegular.png")
    assert repobuild.route("playmats", None, "Blue.png", "alt", True) == (
        "playmats", "Playmats/Blue.png")
    assert repobuild.route("backgrounds", None, "background.jpg", "alt", True) == (
        "playmats", "background.jpg")
    assert repobuild.route("other", None, "readme.md", "alt", True) == (None, None)


def test_route_no_split_keeps_flat_cards():
    assert repobuild.route("cards", "OP01-001", "OP01-001.png", "alt", False) == (
        "cards-alt", "Cards/OP01/OP01-001.png")


def test_route_unknown_type_bucket():
    fam, rel = repobuild.route("cards", "ZZ99-999", "ZZ99-999.png", "alt", True)
    assert fam == "cards-alt" and rel.startswith("Unknown/Cards/ZZ99/")


# ------------------------------------------------------------------ build (ingest factice)
def _source_tree(root: Path) -> Path:
    make_png(root / "Cards" / "OP01" / "OP01-001.png")             # leader
    make_png(root / "Cards" / "OP01" / "OP01-016_alt.png")         # character (parasite)
    make_png(root / "Cards" / "Don" / "Don.png")                  # don
    make_png(root / "Playmats" / "Blue.png", 1920, 1080)          # playmat
    make_png(root / "CardBacks" / "CardBackRegular.png")          # dos
    (root / "TRANSLATION.txt").write_text("Key=Val\n")            # traduction
    (root / "README.md").write_text("ignore")                    # other -> non classé
    return root


def test_build_routes_into_family_repos(tmp_path):
    src = _source_tree(tmp_path / "src")
    fake_ingest = lambda source, wd, on_progress=None: src
    out = tmp_path / "out"
    rep = repobuild.build(["dummy://src"], out, cards_as="alt",
                          ingest=fake_ingest, git_init=False)

    assert (out / "cards-alt" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    assert (out / "cards-alt" / "Characters" / "Cards" / "OP01" / "OP01-016.png").is_file()
    assert (out / "cardbacks-don" / "Cards" / "Don" / "Don.png").is_file()
    assert (out / "cardbacks-don" / "CardBacks" / "CardBackRegular.png").is_file()
    assert (out / "playmats" / "Playmats" / "Blue.png").is_file()
    assert (out / "translations" / "TRANSLATION.txt").is_file()
    # README.md source non reconnu -> non classé, pas copié
    assert rep.unclassified and rep.unclassified[0]["path"] == "README.md"

    # MANIFEST par dépôt, avec compte par type pour les cartes
    man = json.loads((out / "cards-alt" / "MANIFEST.json").read_text())
    assert man["family"] == "cards-alt" and man["by_type"] == {"Leaders": 1, "Characters": 1}
    assert man["files"] == 2


def test_build_collision_last_source_wins(tmp_path):
    a = _minimal_card_src(tmp_path / "a", "OP01-001.png")
    b = _minimal_card_src(tmp_path / "b", "OP01-001.png")
    srcs = {"src://a": a, "src://b": b}
    fake_ingest = lambda source, wd, on_progress=None: srcs[source]
    out = tmp_path / "out"
    rep = repobuild.build(["src://a", "src://b"], out, ingest=fake_ingest, git_init=False)
    assert rep.collisions and rep.collisions[0]["repo"] == "cards-alt"


def _minimal_card_src(root: Path, filename: str) -> Path:
    make_png(root / "Cards" / "OP01" / filename)
    return root


def test_build_git_init_creates_repo_and_gitignore(tmp_path):
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    repobuild.build(["s"], out, ingest=lambda s, wd, on_progress=None: src, git_init=True)
    assert (out / "cards-alt" / ".gitignore").is_file()
    # .git présent seulement si git est installé ; on ne l'exige pas


# ------------------------------------------------------------------ Google Drive
@pytest.mark.parametrize("url,expected", [
    ("https://drive.google.com/file/d/1AbC_dEF-9/view?usp=sharing", "1AbC_dEF-9"),
    ("https://drive.google.com/open?id=XYZ123", "XYZ123"),
    ("https://drive.usercontent.google.com/download?id=Q9&export=download", "Q9"),
])
def test_drive_id_parsing(url, expected):
    assert packlib.is_drive_url(url)
    assert packlib._drive_id(url) == expected


def test_ingest_drive_single_png(tmp_path, monkeypatch):
    # simule un téléchargement Drive d'une image unique (pas un zip)
    def fake_dl(file_id, dest, timeout=60.0, on_progress=packlib._noop_progress):
        make_png(dest)
        return "OP01-001.png"
    monkeypatch.setattr(packlib, "_drive_download", fake_dl)
    out = packlib.ingest("https://drive.google.com/file/d/ID/view", tmp_path / "w")
    assert (out / "OP01-001.png").is_file()
