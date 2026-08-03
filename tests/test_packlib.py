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


# ------------------------------------------------------------------ P13.1 : nom de pack hostile
# Le nom de pack sert de nom de DOSSIER, et `normalize()` fait `rmtree` dessus. Un nom qui
# s'échappe de la bibliothèque effacerait `~/.optcgsim-studio` : backups pristine (donc plus
# aucun `restore-all` possible), manifeste et `studio.db`. Le nom peut venir du `label` d'un
# `collection.json` distant, pas seulement du clavier.
@pytest.mark.parametrize("hostile", ["..", "../..", "../evil", ".", ""])
def test_normalize_refuses_name_escaping_library(install, lib, tmp_path, hostile):
    lib.mkdir(parents=True)
    sentinel = lib.parent / "studio.db"                 # voisin de la bibliothèque
    sentinel.write_text("mes decks")
    (lib / "PackExistant").mkdir()
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 960, 1342)

    with pytest.raises(packlib.PackError, match="invalide"):
        packlib.normalize(src, install, hostile, "x", lib)

    assert sentinel.read_text() == "mes decks"          # rien n'a été effacé
    assert (lib / "PackExistant").is_dir()
    assert lib.is_dir()


def test_normalize_accepts_ordinary_names(install, lib, tmp_path):
    """Non-régression : les noms normaux (espaces, accents, tirets) passent toujours."""
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    pack, rep = packlib.normalize(src, install, "Alt arts FR - v2", "x", lib)
    assert pack == lib / "Alt arts FR - v2"
    assert "OP01-001" in rep.cards


def test_add_pack_sanitizes_explicit_name(install, lib, tmp_path):
    """Un `name` EXPLICITE est assaini lui aussi (il vient parfois d'une source distante)."""
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 960, 1342)
    pack, rep = packlib.add_pack(src, install, name="../../evil", lib_dir=lib,
                                 work_dir=tmp_path / "src")
    # `..` peut subsister comme TEXTE (`_.._evil`) : ce qui compte est qu'il ne reste aucun
    # séparateur, donc un seul composant de chemin, donc aucune remontée possible.
    assert pack.parent == lib
    assert pack.relative_to(lib) == Path(pack.name)
    assert "/" not in pack.name and pack.name not in ("..", ".")


def test_safe_pack_name_neutralises_path_separators():
    assert packlib.safe_pack_name("..") == "pack"
    assert packlib.safe_pack_name("../../etc") == "_.._etc"
    assert packlib.safe_pack_name("/etc/passwd") == "_etc_passwd"
    assert packlib.safe_pack_name("Alt arts FR") == "Alt arts FR"     # inchangé


# ---------------------- P15.7 : _repair_corrupted garde son exigence STRICTE (tout ou rien)
def test_repair_corrupted_stays_strict_on_partial_success(tmp_path, monkeypatch):
    """Contrairement à `add_pack`/`repos build`, une RÉPARATION partielle n'est pas une
    réparation : si un seul fichier corrompu ne peut être re-téléchargé, `_repair_corrupted`
    doit échouer entièrement (retour False -> repli sur un nouveau téléchargement complet),
    pas déclarer le dépôt sain avec un fichier toujours cassé dedans."""
    from studio.assets import packlib, sourcefetch

    appels = {}

    def fake_fetch(url, paths, dest, token=None, on_progress=None, strict=True, failed=None):
        appels["strict"] = strict
        raise sourcefetch.FetchError("simulé : un des deux fichiers reste introuvable")

    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)
    ok = packlib._repair_corrupted(tmp_path / "out", ["a.png", "b.png"],
                                   "https://github.com/o/r", None, packlib._noop_progress)

    assert ok is False, "un échec partiel doit faire échouer TOUTE la réparation"
    assert appels["strict"] is True, "_repair_corrupted doit demander le mode STRICT"


# ----------------------------------------------------- LOT D : URL de deckpack.json nu
# Avant : coller l'URL d'un `deckpack.json` (le cas le plus évident pour un utilisateur)
# échouait avec un message trompeur — `_materialize` copiait le JSON sous `download.zip` et
# `find_manifest` ne trouvait rien. Désormais `ingest` détecte le JSON (URL + Content-Type +
# 1er octet) et le matérialise sous `deckpack.json`.
def _fake_download_writing(payload: bytes, content_type: str | None = None):
    """Fabrique un `_download` qui écrit `payload` sur disque et renvoie le Content-Type."""
    def fake(url, dest_zip, timeout=60.0, on_progress=packlib._noop_progress):
        dest_zip.write_bytes(payload)
        return content_type
    return fake


def test_ingest_http_json_url_materialized_as_deckpack_json(tmp_path, monkeypatch):
    """Une URL https://…/deckpack.json est matérialisée sous ce nom (le cas d'usage visé)."""
    blob = b'{"name":"P","decks":[]}'
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(blob, "application/json"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/deckpack.json", tmp_path / "w")
    assert (out / "deckpack.json").read_bytes() == blob


def test_ingest_http_json_url_detected_by_first_byte_when_no_extension(tmp_path, monkeypatch):
    """URL sans extension `.json` et Content-Type générique : le 1er octet `{` suffit.

    Cas réel : un CDN/raw GitHub servant du JSON sans extension, ou avec un Content-Type
    `text/plain` (certains serveurs mal configurés). Le contenu est le signal le plus sûr."""
    blob = b'  \n\t{"name":"P","decks":[]}'   # espaces de tête tolérés
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(blob, "text/plain"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/raw/pack", tmp_path / "w")
    assert (out / "deckpack.json").read_bytes() == blob


def test_ingest_http_json_url_with_query_string_extension(tmp_path, monkeypatch):
    """Une URL portant une query string `?token=…` reste détectée via le chemin de l'URL."""
    blob = b'{"name":"P","decks":[]}'
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(blob, "application/json"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/pack.json?token=abc&dl=1", tmp_path / "w")
    assert (out / "deckpack.json").exists()


def test_ingest_http_json_url_with_json_content_type_only(tmp_path, monkeypatch):
    """Content-Type `application/json` seul (URL sans `.json`) suffit à détecter le JSON."""
    blob = b'{"name":"P","decks":[]}'
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(blob, "application/json; charset=utf-8"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/api/pack", tmp_path / "w")
    assert (out / "deckpack.json").exists()


def test_ingest_http_zip_url_unchanged_by_json_detection(tmp_path, monkeypatch):
    """Non-régression : une URL de .zip reste extraite (le 1er octet `PK` n'est pas `{`)."""
    src = tmp_path / "content"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    zpath = tmp_path / "pack.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.write(src / "Cards" / "OP01" / "OP01-001.png", "Cards/OP01/OP01-001.png")
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(zpath.read_bytes(), "application/zip"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/pack.zip", tmp_path / "w")
    assert (out / "Cards" / "OP01" / "OP01-001.png").exists()
    assert not (out / "deckpack.json").exists()


def test_ingest_http_non_json_non_zip_file_keeps_legacy_name(tmp_path, monkeypatch):
    """Non-régression : un fichier unique ni zip ni JSON reste copié sous `download.zip`
    (comportement d'avant — une image partagée par URL n'est pas un pack, on ne le devine
    pas en `deckpack.json`)."""
    png = tmp_path / "x.png"
    make_png(png)
    monkeypatch.setattr(packlib, "_download",
                        _fake_download_writing(png.read_bytes(), "image/png"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    out = packlib.ingest("https://site.example/card.png", tmp_path / "w")
    assert (out / "download.zip").exists()      # nom historique préservé
    assert not (out / "deckpack.json").exists()


def test_looks_like_json_unit_signals(tmp_path):
    """Les trois signaux de `_looks_like_json` couverts isolément + les faux positifs exclus."""
    p = tmp_path / "blob"
    p.write_bytes(b'{"a":1}')
    assert packlib._looks_like_json(p, "https://x/y.json", None)
    assert packlib._looks_like_json(p, "https://x/y", "application/json")
    assert packlib._looks_like_json(p, "https://x/y", "application/vnd.deckpack+json")
    assert packlib._looks_like_json(p, "https://x/y", None)            # 1er octet '{'
    p.write_bytes(b"  \r\n\t{")
    assert packlib._looks_like_json(p, "https://x/y", None)            # espaces de tête
    # Faux positifs exclus : zip (PK), image (PNG), texte non-JSON.
    p.write_bytes(b"PK\x03\x04")
    assert not packlib._looks_like_json(p, "https://x/y.zip", "application/zip")
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    assert not packlib._looks_like_json(p, "https://x/y.png", "image/png")
    p.write_bytes(b"not json at all")
    assert not packlib._looks_like_json(p, "https://x/y.txt", "text/plain")
