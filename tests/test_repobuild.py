"""Tests du constructeur de dépôts d'images (repobuild) — routage par famille + cartes/type,
génération hors-ligne (ingest factice), collisions, non-classés, et parsing/ingest Drive."""

import json
import struct
import zipfile
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


def test_route_translation_uses_lang_not_cards_as():
    # `lang` est un axe INDÉPENDANT de `cards_as` : la traduction ne suit plus l'alias fixe.
    assert repobuild.route("translation", None, "TRANSLATION.txt", "alt", True, lang="fr") == (
        "translations-fr", "TRANSLATION.txt")
    assert repobuild.route("translation", None, "TRANSLATION.txt", "translated-alt", True,
                           lang="fr") == ("translations-fr", "TRANSLATION.txt")
    assert repobuild.route("translation", None, "TRANSLATION.txt", "translated", True,
                           lang="es") == ("translations-es", "TRANSLATION.txt")
    # sans lang : comportement inchangé (alias fixe "translations")
    assert repobuild.route("translation", None, "TRANSLATION.txt", "alt", True) == (
        "translations", "TRANSLATION.txt")


def test_route_don_goes_to_cards_family_not_cardbacks():
    # DON!! alternatif = un reskin de CARTE, pas un dos de carte -> famille cartes, type "Don".
    assert repobuild.route("don", None, "Don.png", "alt", True) == (
        "cards-alt", "Don/Cards/Don/Don.png")
    assert repobuild.route("don", None, "Don_Wano.png", "translated", True) == (
        "translations", "Don/Cards/Don/Don_Wano.png")


def test_route_fixed_families():
    assert repobuild.route("cardbacks", None, "CardBackRegular.png", "alt", True) == (
        "cardbacks", "CardBacks/CardBackRegular.png")
    assert repobuild.route("playmats", None, "Blue.png", "alt", True) == (
        "playmats", "Playmats/Blue.png")
    assert repobuild.route("backgrounds", None, "background.jpg", "alt", True) == (
        "playmats", "background.jpg")
    assert repobuild.route("other", None, "readme.md", "alt", True) == (None, None)


def test_route_no_split_keeps_flat_cards():
    assert repobuild.route("cards", "OP01-001", "OP01-001.png", "alt", False) == (
        "cards-alt", "Cards/OP01/OP01-001.png")
    assert repobuild.route("don", None, "Don.png", "alt", False) == (
        "cards-alt", "Cards/Don/Don.png")


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
    # DON!! = un reskin de CARTE (pas un dos de carte) -> famille cartes, type "Don".
    assert (out / "cards-alt" / "Don" / "Cards" / "Don" / "Don.png").is_file()
    assert not (out / "cardbacks-don").exists()
    assert (out / "cardbacks" / "CardBacks" / "CardBackRegular.png").is_file()
    assert (out / "playmats" / "Playmats" / "Blue.png").is_file()
    assert (out / "translations" / "TRANSLATION.txt").is_file()
    # README.md source non reconnu -> non classé, pas copié
    assert rep.unclassified and rep.unclassified[0]["path"] == "README.md"

    # MANIFEST par dépôt, avec compte par type pour les cartes (Don inclus)
    man = json.loads((out / "cards-alt" / "MANIFEST.json").read_text())
    assert man["family"] == "cards-alt"
    assert man["by_type"] == {"Leaders": 1, "Characters": 1, "Don": 1}
    assert man["file_count"] == 3
    # premier build : tout est "ajouté", rien de modifié/orphelin
    assert sorted(rep.repos["cards-alt"].added) == [
        "Characters/Cards/OP01/OP01-016.png", "Don/Cards/Don/Don.png",
        "Leaders/Cards/OP01/OP01-001.png"]
    assert rep.repos["cards-alt"].changed == [] and rep.repos["cards-alt"].orphans == []
    # cardbacks reste un dépôt à part, dédié aux VRAIS dos de cartes
    cb = json.loads((out / "cardbacks" / "MANIFEST.json").read_text())
    assert cb["file_count"] == 1 and cb["by_type"] == {}


def test_build_theme_junk_txt_files_never_pollute_translations(tmp_path):
    """Régression EXACTE de l'incident réel : un thème (lien direct, aucune traduction) contient
    un README d'installation et un placeholder texte ; aucun des deux ne doit atterrir dans
    translations/ (dépôt qui ne devrait alors même pas exister, faute de vraie traduction)."""
    src = tmp_path / "theme"
    make_png(src / "Playmats" / "Blue.png", 1920, 1080)
    make_png(src / "CardBacks" / "CardBackRegular.png")
    (src / "instructions.txt").write_text(
        "*** How to Install ***\n\n1. Copy the contents to your install's StreamingAssets…")
    (src / "CardBackRegular.txt").write_text("test")   # erreur d'extension côté auteur du thème
    out = tmp_path / "out"
    rep = repobuild.build(["theme-link"], out, cards_as="alt",
                          ingest=lambda s, wd, on_progress=None: src, git_init=False)

    assert not (out / "translations").exists()   # aucune vraie traduction -> pas de dépôt
    assert "playmats" in rep.repos and "cardbacks" in rep.repos
    unclassified_paths = {u["path"] for u in rep.unclassified}
    assert unclassified_paths == {"instructions.txt", "CardBackRegular.txt"}


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


# ------------------------------------------------------------------ P8+ : mise à jour (repos update)
def test_rebuild_same_source_is_not_a_false_collision(tmp_path):
    """Relancer build() sur un --out DÉJÀ construit ne doit PAS signaler chaque fichier
    existant comme une collision (bug initial : collision détectée sur dest.exists())."""
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    fake_ingest = lambda s, wd, on_progress=None: src
    rep1 = repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)
    rep2 = repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)
    assert rep1.collisions == [] and rep2.collisions == []


def test_rebuild_reports_added_changed_orphans(tmp_path):
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")   # leader, inchangé
    out = tmp_path / "out"
    fake_ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)

    # nouvelle "sortie de set" : le leader change de version + un event apparaît + rien d'autre
    make_png(src / "Cards" / "OP01" / "OP01-001.png", 500, 700)   # même id, contenu différent
    make_png(src / "Cards" / "OP14" / "OP14-018.png")             # event : nouveau
    rep2 = repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)

    stat = rep2.repos["cards-alt"]
    assert stat.added == ["Events/Cards/OP14/OP14-018.png"]
    assert stat.changed == ["Leaders/Cards/OP01/OP01-001.png"]
    assert stat.orphans == []


def test_rebuild_reports_orphan_when_source_drops_a_file(tmp_path):
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    make_png(src / "Cards" / "OP01" / "OP01-016.png")
    out = tmp_path / "out"
    fake_ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)

    (src / "Cards" / "OP01" / "OP01-016.png").unlink()   # retiré de la source
    rep2 = repobuild.build(["s"], out, ingest=fake_ingest, git_init=False)

    stat = rep2.repos["cards-alt"]
    assert stat.orphans == ["Characters/Cards/OP01/OP01-016.png"]
    # jamais supprimé sur disque, juste signalé
    assert (out / "cards-alt" / "Characters" / "Cards" / "OP01" / "OP01-016.png").is_file()


def test_build_records_log_for_update(tmp_path):
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    repobuild.build(["s"], out, cards_as="alt", split_cards_by_type=True,
                    ingest=lambda s, wd, on_progress=None: src, git_init=False)
    log = repobuild.load_build_log(out)
    assert log == [{"sources": ["s"], "cards_as": "alt", "split_cards_by_type": True,
                    "path_prefix": None, "lang": None,
                    "collection_label": None, "collection_group": None}]
    # (out / ".repos-build.json") reste HORS des dépôts de famille -> jamais poussé avec eux
    assert not (out / "cards-alt" / ".repos-build.json").exists()


def test_build_log_dedupes_by_config_not_by_sources(tmp_path):
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, cards_as="alt", ingest=ingest, git_init=False)
    repobuild.build(["s", "s2"], out, cards_as="alt", ingest=ingest, git_init=False)
    log = repobuild.load_build_log(out)
    assert len(log) == 1 and log[0]["sources"] == ["s", "s2"]   # la 2e config remplace la 1re


# ------------------------------------------------------------------ P8+ : --path-prefix (variantes)
def _fr_mixed_source(root: Path) -> Path:
    """Une seule source qui mélange DEUX variantes du même id (classique/alternative) — le
    cas réel signalé : un repo FR github avec traductions + cartes classiques + alternatives."""
    make_png(root / "FR_classique" / "OP01" / "OP01-001_OVERRIDE.png")
    make_png(root / "FR_alt" / "OP01" / "OP01-001_alt.png")
    (root / "TRANSLATION.txt").write_text("Key=Val\n")
    return root


def test_mixed_variants_in_one_build_collide(tmp_path):
    """Sans path_prefix, classique et alternative du MÊME id se disputent le même nom
    canonique dans le même run -> collision (dernière source gagne), PERTE silencieuse
    d'une des deux variantes si on ne s'en aperçoit pas."""
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    rep = repobuild.build(["fr"], out, cards_as="translated",
                          ingest=lambda s, wd, on_progress=None: src, git_init=False)
    assert rep.collisions and rep.collisions[0]["repo"] == "translations"


def test_path_prefix_splits_variants_into_separate_families(tmp_path):
    """Deux build() scopés chacun à son sous-dossier, avec un cards_as distinct par variante
    -> les deux images du MÊME id coexistent, chacune dans sa famille."""
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    rep_classic = repobuild.build(["fr"], out, cards_as="translated",
                                  path_prefix="FR_classique", ingest=ingest, git_init=False)
    rep_alt = repobuild.build(["fr"], out, cards_as="translated-alt",
                              path_prefix="FR_alt", ingest=ingest, git_init=False)

    assert rep_classic.collisions == [] and rep_alt.collisions == []
    assert (out / "translations" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    assert (out / "translated-alt" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    # chaque build exclut la variante CARTE hors de son préfixe (l'AUTRE variante)…
    assert rep_classic.excluded_by_prefix == 1   # FR_alt/OP01-001_alt.png
    assert rep_alt.excluded_by_prefix == 1        # FR_classique/OP01-001_OVERRIDE.png
    # …mais PAS l'asset partagé (TRANSLATION.txt, à la racine, hors des deux dossiers) : il
    # n'a aucun risque de collision de variante, donc --path-prefix ne l'exclut jamais.
    assert (out / "translations" / "TRANSLATION.txt").is_file()


def test_path_prefix_never_excludes_shared_translation_file(tmp_path):
    """Régression exacte du doute exprimé par l'utilisateur : un TRANSLATION.txt à la racine
    (hors de tout dossier de variante) doit être inclus dans CHAQUE build scopé par
    --path-prefix, pas seulement dans un build « sans préfixe » séparé."""
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    rep = repobuild.build(["fr"], out, cards_as="translated",
                          path_prefix="FR_classique", ingest=ingest, git_init=False)
    assert (out / "translations" / "TRANSLATION.txt").read_text() == "Key=Val\n"
    # le fichier partagé n'est jamais compté comme "hors périmètre"
    assert rep.excluded_by_prefix == 1   # uniquement FR_alt/OP01-001_alt.png


def test_path_prefix_recorded_and_replayed_by_update(tmp_path):
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    calls = []

    def fake_ingest(source, wd, on_progress=None):
        calls.append(source)
        return src

    repobuild.build(["fr"], out, cards_as="translated", path_prefix="FR_classique",
                    ingest=fake_ingest, git_init=False)
    repobuild.build(["fr"], out, cards_as="translated-alt", path_prefix="FR_alt",
                    ingest=fake_ingest, git_init=False)
    log = repobuild.load_build_log(out)
    assert {e["path_prefix"] for e in log} == {"FR_classique", "FR_alt"}

    calls.clear()
    reports = repobuild.update(out, ingest=fake_ingest)
    assert len(reports) == 2 and calls == ["fr", "fr"]
    fams = {fam for r in reports for fam in r.repos}
    assert fams == {"translations", "translated-alt"}


# ------------------------------------------------------------------ P8+ : --lang (traduction découplée)
def test_two_variants_same_lang_converge_into_one_translation_repo(tmp_path):
    """Classique et full-art, deux --cards-as distincts, mais le MÊME --lang fr : leurs
    TRANSLATION.txt convergent dans un seul dépôt translations-fr (pas un par variante)."""
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["fr"], out, cards_as="translated-fr-classic", path_prefix="FR_classique",
                    lang="fr", ingest=ingest, git_init=False)
    repobuild.build(["fr"], out, cards_as="translated-fr-fullart", path_prefix="FR_alt",
                    lang="fr", ingest=ingest, git_init=False)

    # les cartes restent séparées par variante d'art...
    assert (out / "translated-fr-classic" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    assert (out / "translated-fr-fullart" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    # ...mais la traduction est regroupée dans UN SEUL dépôt de langue, pas dupliquée par variante.
    assert (out / "translations-fr" / "TRANSLATION.txt").is_file()
    assert not (out / "translated-fr-classic" / "TRANSLATION.txt").exists()
    assert not (out / "translated-fr-fullart" / "TRANSLATION.txt").exists()


def test_different_langs_never_collide():
    fr = repobuild.route("translation", None, "TRANSLATION.txt", "translated", True, lang="fr")
    es = repobuild.route("translation", None, "TRANSLATION.txt", "translated", True, lang="es")
    assert fr[0] != es[0]   # translations-fr vs translations-es : jamais le même dépôt


def test_lang_recorded_and_replayed_by_update(tmp_path):
    src = _fr_mixed_source(tmp_path / "src")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["fr"], out, cards_as="translated", path_prefix="FR_classique",
                    lang="fr", ingest=ingest, git_init=False)
    log = repobuild.load_build_log(out)
    assert log[0]["lang"] == "fr"

    reports = repobuild.update(out, ingest=ingest)
    assert reports[0].repos.get("translations-fr") is not None


def test_update_replays_recorded_sources_without_reasking(tmp_path):
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    out = tmp_path / "out"
    calls = []

    def fake_ingest(source, wd, on_progress=None):
        calls.append(source)
        return src

    repobuild.build(["my-drive-link"], out, cards_as="alt", ingest=fake_ingest, git_init=False)
    calls.clear()
    make_png(src / "Cards" / "OP14" / "OP14-018.png")   # nouvelle sortie de set
    reports = repobuild.update(out, ingest=fake_ingest)

    assert calls == ["my-drive-link"]         # source rejouée SANS la repasser explicitement
    assert len(reports) == 1
    assert reports[0].repos["cards-alt"].added == ["Events/Cards/OP14/OP14-018.png"]


def test_update_without_prior_build_raises(tmp_path):
    with pytest.raises(packlib.PackError, match="historique"):
        repobuild.update(tmp_path / "never-built")


def test_rebuild_over_legacy_manifest_with_int_files(tmp_path):
    """Régression : un MANIFEST.json d'avant `repos update` avait "files" = ENTIER (compte).
    Rebuild ne doit pas planter (TypeError: 'int' object is not iterable) mais repartir
    d'un diff propre (tout en 'ajouté')."""
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    # simule un ancien dépôt : MANIFEST avec l'ancien schéma ("files": <int>)
    legacy = out / "cards-alt"
    legacy.mkdir(parents=True)
    (legacy / "MANIFEST.json").write_text(json.dumps({"family": "cards-alt", "files": 42}))

    rep = repobuild.build(["s"], out, ingest=lambda s, wd, on_progress=None: src, git_init=False)
    assert rep.repos["cards-alt"].added == ["Leaders/Cards/OP01/OP01-001.png"]
    assert rep.repos["cards-alt"].orphans == []      # l'entier n'est pas traité comme des chemins
    man = json.loads((legacy / "MANIFEST.json").read_text())
    assert isinstance(man["files"], dict)            # migré vers la map chemin->sha1


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


# ------------------------------------------------------------------ P8+ : fetch sélectif GitHub
# (build() télécharge par fichier au lieu du zip entier quand path_prefix scope un dépôt
# GitHub explorable — évite de retélécharger un dépôt de plusieurs Go pour n'en garder qu'une
# fraction, et réduit l'exposition à une corruption réseau en cours de route, cf. l'incident
# réel : "Bad CRC-32" au milieu de l'extraction d'un zip FR de 2,2 Go.)
def test_build_uses_selective_fetch_when_github_and_prefix(tmp_path, monkeypatch):
    from studio.assets import sourcefetch
    remote = [sourcefetch.RemoteFile("FR_classique/OP01/OP01-001_OVERRIDE.png", 100),
              sourcefetch.RemoteFile("FR_alt/OP01/OP01-001_alt.png", 100),
              sourcefetch.RemoteFile("TRANSLATION.txt", 10)]
    monkeypatch.setattr(sourcefetch, "list_remote_files", lambda url, token=None: remote)
    fetched = {}

    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        fetched["paths"] = sorted(paths)
        for p in paths:
            (Path(dest) / p).parent.mkdir(parents=True, exist_ok=True)
            if p.endswith(".txt"):
                (Path(dest) / p).write_text("Key=Val\n")
            else:
                make_png(Path(dest) / p)
        return Path(dest)

    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)

    out = tmp_path / "out"
    rep = repobuild.build(["https://github.com/o/r"], out, cards_as="translated",
                          path_prefix="FR_classique", git_init=False)
    # seuls les fichiers du périmètre (FR_classique + l'asset partagé) ont été TÉLÉCHARGÉS —
    # FR_alt/OP01-001_alt.png n'a jamais quitté GitHub, pas juste filtré après coup.
    assert fetched["paths"] == ["FR_classique/OP01/OP01-001_OVERRIDE.png", "TRANSLATION.txt"]
    assert (out / "translations" / "Leaders" / "Cards" / "OP01" / "OP01-001.png").is_file()
    assert (out / "translations" / "TRANSLATION.txt").is_file()


def test_build_passes_token_to_selective_fetch(tmp_path, monkeypatch):
    from studio.assets import sourcefetch
    remote = [sourcefetch.RemoteFile("FR_classique/OP01/OP01-001_OVERRIDE.png", 100)]
    seen_tokens = {}

    def fake_list(url, token=None):
        seen_tokens["list"] = token
        return remote

    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        seen_tokens["fetch"] = token
        for p in paths:
            make_png(Path(dest) / p)
        return Path(dest)

    monkeypatch.setattr(sourcefetch, "list_remote_files", fake_list)
    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)
    repobuild.build(["https://github.com/o/r"], tmp_path / "out", cards_as="translated",
                    path_prefix="FR_classique", token="ghp_secret", git_init=False)
    assert seen_tokens == {"list": "ghp_secret", "fetch": "ghp_secret"}


def test_build_falls_back_to_full_ingest_when_source_not_explorable(tmp_path, monkeypatch):
    """Dropbox (et tout ce qui n'est pas github.com) : list_remote_files renvoie None ->
    repli sur `ingest` complet, comme avant l'ajout du fetch sélectif."""
    from studio.assets import sourcefetch
    monkeypatch.setattr(sourcefetch, "list_remote_files", lambda url, token=None: None)
    # préfixe qui matche réellement le contenu (Cards/OP01/...), pour vérifier que le
    # fallback ingest() COMPLET s'est bien produit (le fichier local est présent et gardé),
    # pas que le prefix a tout filtré de toute façon.
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    rep = repobuild.build(["https://dropbox.com/x"], tmp_path / "out", cards_as="alt",
                          path_prefix="Cards",
                          ingest=lambda s, wd, on_progress=None: src, git_init=False)
    assert rep.repos["cards-alt"].files == 1


def test_build_without_path_prefix_never_calls_selective_fetch(tmp_path, monkeypatch):
    """Sans path_prefix, rien à économiser (tout serait gardé) -> pas de fetch sélectif,
    même pour une URL GitHub-shaped ; l'`ingest` injecté est utilisé normalement."""
    from studio.assets import sourcefetch
    called = []
    monkeypatch.setattr(sourcefetch, "list_remote_files",
                        lambda url, token=None: called.append(url) or None)
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    repobuild.build(["https://github.com/o/r"], tmp_path / "out", cards_as="alt",
                    ingest=lambda s, wd, on_progress=None: src, git_init=False)
    assert called == []


# ------------------------------------------------------------------ P8+ : corruption réseau (retry)
def _make_zip_with_corrupt_member(path: Path, good_name: str, good_content: bytes,
                                  bad_name: str, bad_content: bytes) -> None:
    """Construit un zip STORED (non compressé) avec un membre au CRC volontairement invalide —
    reproduit précisément l'incident réel (central directory valide, données d'UN membre
    corrompues en transit) sans dépendre du réseau. On flippe un octet dans la zone de
    données du membre visé (après son en-tête local), en laissant le CRC déclaré (header
    local + central directory) intact -> mismatch CRC garanti à la lecture."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr(good_name, good_content)
        zf.writestr(bad_name, bad_content)
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo(bad_name)
    data = bytearray(path.read_bytes())
    namelen, extralen = struct.unpack("<HH", data[info.header_offset + 26:info.header_offset + 30])
    data_start = info.header_offset + 30 + namelen + extralen
    data[data_start] ^= 0xFF
    path.write_bytes(bytes(data))


def test_safe_extract_tolerates_one_bad_member_and_reports_it(tmp_path):
    zpath = tmp_path / "a.zip"
    _make_zip_with_corrupt_member(zpath, "Cards/OP01/OP01-001.png", b"GOODBYTES",
                                  "Cards/OP01/OP01-002.png", b"BADBYTESXX")
    out = tmp_path / "out"
    with zipfile.ZipFile(zpath) as zf:
        corrupted = packlib._safe_extract(zf, out)
    # le membre en défaut est signalé, PAS une exception qui interromprait tout —
    # et le membre SAIN, lui, s'est bien extrait.
    assert corrupted == ["Cards/OP01/OP01-002.png"]
    assert (out / "Cards" / "OP01" / "OP01-001.png").read_bytes() == b"GOODBYTES"


def test_repair_corrupted_patches_via_sourcefetch(tmp_path, monkeypatch):
    from studio.assets import sourcefetch
    patched = {}

    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        patched["paths"] = paths
        for p in paths:
            (Path(dest) / p).parent.mkdir(parents=True, exist_ok=True)
            (Path(dest) / p).write_bytes(b"PATCHED")
        return Path(dest)

    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)
    out = tmp_path / "out"
    out.mkdir()
    ok = packlib._repair_corrupted(out, ["Cards/OP01/OP01-002.png"],
                                   "https://github.com/o/r", None, packlib._noop_progress)
    assert ok is True
    assert patched["paths"] == ["Cards/OP01/OP01-002.png"]
    assert (out / "Cards" / "OP01" / "OP01-002.png").read_bytes() == b"PATCHED"


def test_repair_corrupted_returns_false_when_not_explorable(tmp_path):
    # Dropbox (et tout ce qui n'est pas github.com) -> fetch_selected lève FetchError direct.
    ok = packlib._repair_corrupted(tmp_path, ["x.png"], "https://dropbox.com/x", None,
                                   packlib._noop_progress)
    assert ok is False


def test_ingest_patches_corrupted_member_instead_of_redownloading_everything(tmp_path, monkeypatch):
    """LE test de la demande utilisateur : sur une source GitHub, un membre en CRC invalide
    déclenche un patch CIBLÉ (re-fetch de CE seul fichier) — le zip de plusieurs Go n'est PAS
    retéléchargé en entier une seconde fois."""
    from studio.assets import sourcefetch
    downloads = []

    def fake_download(url, dest_zip, timeout=60.0, on_progress=packlib._noop_progress):
        downloads.append(url)
        _make_zip_with_corrupt_member(dest_zip, "Cards/OP01/OP01-001.png", b"GOODBYTES",
                                      "Cards/OP01/OP01-002.png", b"BADBYTESXX")

    patched = {}

    def fake_fetch(url, paths, dest, token=None, on_progress=None):
        patched["paths"] = paths
        for p in paths:
            (Path(dest) / p).parent.mkdir(parents=True, exist_ok=True)
            (Path(dest) / p).write_bytes(b"REPAIRED!!")
        return Path(dest)

    monkeypatch.setattr(packlib, "_download", fake_download)
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    monkeypatch.setattr(sourcefetch, "fetch_selected", fake_fetch)

    out = packlib.ingest("https://github.com/Sparklight-TL/OPTCGSim_FR", tmp_path / "w")

    assert len(downloads) == 1   # UN seul téléchargement complet, jamais retenté
    assert patched["paths"] == ["Cards/OP01/OP01-002.png"]   # QUE le membre en défaut
    assert (out / "Cards" / "OP01" / "OP01-001.png").read_bytes() == b"GOODBYTES"     # intact
    assert (out / "Cards" / "OP01" / "OP01-002.png").read_bytes() == b"REPAIRED!!"    # patché


def test_ingest_falls_back_to_redownload_when_repair_not_possible(tmp_path, monkeypatch):
    """Source non explorable (ex. Dropbox) : le patch ciblé est impossible -> repli normal
    sur un nouveau téléchargement complet (comportement inchangé)."""
    downloads = []

    def fake_download(url, dest_zip, timeout=60.0, on_progress=packlib._noop_progress):
        downloads.append(url)
        if len(downloads) < 2:
            _make_zip_with_corrupt_member(dest_zip, "Cards/OP01/OP01-001.png", b"GOODBYTES",
                                          "Cards/OP01/OP01-002.png", b"BADBYTESXX")
        else:
            with zipfile.ZipFile(dest_zip, "w") as zf:
                zf.writestr("Cards/OP01/OP01-001.png", b"GOODBYTES")
                zf.writestr("Cards/OP01/OP01-002.png", b"FIXED-ON-RETRY")

    monkeypatch.setattr(packlib, "_download", fake_download)
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    monkeypatch.setattr(packlib.time, "sleep", lambda s: None)
    out = packlib.ingest("https://dropbox.com/s/x/pack.zip", tmp_path / "w")
    assert len(downloads) == 2   # patch impossible (pas GitHub) -> retéléchargement complet
    assert (out / "Cards" / "OP01" / "OP01-002.png").read_bytes() == b"FIXED-ON-RETRY"


# Un zip structurellement invalide ne lève PAS BadZipFile (`zipfile.is_zipfile` renvoie
# juste False, sans exception). Les deux tests suivants couvrent l'orchestration du retry
# via `_materialize` mocké (plus simple qu'une double corruption byte-exacte).
def test_ingest_retries_on_bad_zip_then_succeeds(tmp_path, monkeypatch):
    """Régression de l'incident réel (BadZipFile 'Bad CRC-32' en pleine extraction d'un gros
    zip GitHub) : `ingest` retente le téléchargement complet plutôt que de planter direct."""
    downloads = []
    materialize_calls = []

    def fake_download(url, dest_zip, timeout=60.0, on_progress=packlib._noop_progress):
        downloads.append(url)
        dest_zip.write_bytes(b"placeholder")

    def fake_materialize(archive, out, orig_name, on_progress=packlib._noop_progress):
        materialize_calls.append(archive)
        if len(materialize_calls) < 2:
            raise zipfile.BadZipFile("Bad CRC-32 for file 'x'")
        out.mkdir(parents=True, exist_ok=True)
        make_png(out / "Cards" / "OP01" / "OP01-001.png")
        return out, []   # (dossier, aucun membre corrompu)

    monkeypatch.setattr(packlib, "_download", fake_download)
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    monkeypatch.setattr(packlib, "_materialize", fake_materialize)
    monkeypatch.setattr(packlib.time, "sleep", lambda s: None)   # pas d'attente réelle en test
    out = packlib.ingest("https://example.com/pack.zip", tmp_path / "w")
    assert len(downloads) == 2   # re-téléchargé en entier après l'échec (seul recours possible)
    assert (out / "Cards" / "OP01" / "OP01-001.png").exists()


def test_ingest_gives_up_after_max_attempts_with_clear_error(tmp_path, monkeypatch):
    def always_bad(archive, out, orig_name, on_progress=packlib._noop_progress):
        raise zipfile.BadZipFile("Bad CRC-32 for file 'x'")

    monkeypatch.setattr(packlib, "_download",
                        lambda url, dest_zip, timeout=60.0, on_progress=None: dest_zip.write_bytes(b"x"))
    monkeypatch.setattr(packlib, "_resolve_url", lambda u: u)
    monkeypatch.setattr(packlib, "_materialize", always_bad)
    monkeypatch.setattr(packlib.time, "sleep", lambda s: None)
    with pytest.raises(packlib.PackError, match="coupure réseau"):
        packlib.ingest("https://example.com/pack.zip", tmp_path / "w")


# ------------------------------------------------------------------ P10 (b) : collection.json
# Génération CLI de docs/PLAN-import-packs.md, chantier P10. Volets (c) résolution distante
# et (d) UI restent À FAIRE — voir studio/assets/collections.py pour le format consommé et
# tests/test_collections.py pour son parsing (P10-a, déjà fait, indépendant de ce qui suit).
def test_build_upserts_collection_entry_per_family(tmp_path):
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    repobuild.build(["s"], out, cards_as="translated-fr-classic",
                    collection_label="Cartes FR classiques", collection_group="cards",
                    ingest=lambda s, wd, on_progress=None: src, git_init=False)
    data = json.loads((out / "collection.json").read_text())
    assert data["packs"] == [{"family": "translated-fr-classic", "url": "",
                             "label": "Cartes FR classiques", "variant_group": "cards"}]


def test_build_collection_label_defaults_to_family_name(tmp_path):
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    repobuild.build(["s"], out, cards_as="alt",
                    ingest=lambda s, wd, on_progress=None: src, git_init=False)
    data = json.loads((out / "collection.json").read_text())
    assert data["packs"] == [{"family": "cards-alt", "url": "", "label": "cards-alt",
                             "variant_group": None}]


def test_build_upserts_without_duplicating_same_family(tmp_path):
    """Rebuild de la MÊME famille -> l'entrée est mise à jour (nouveau label), pas dupliquée."""
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, cards_as="alt", collection_label="Premier nom", ingest=ingest,
                    git_init=False)
    repobuild.build(["s"], out, cards_as="alt", collection_label="Nom mis à jour", ingest=ingest,
                    git_init=False)
    data = json.loads((out / "collection.json").read_text())
    assert len(data["packs"]) == 1
    assert data["packs"][0]["label"] == "Nom mis à jour"


def test_build_preserves_manually_filled_url_across_rebuilds(tmp_path):
    """Une URL renseignée à la main (après un vrai `git push`) ne doit JAMAIS être effacée
    par un `repos build`/`update` suivant sur la même famille."""
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, cards_as="alt", ingest=ingest, git_init=False)

    manifest_path = out / "collection.json"
    data = json.loads(manifest_path.read_text())
    data["packs"][0]["url"] = "https://github.com/hquezser/optcgsim-cards-alt"
    manifest_path.write_text(json.dumps(data))

    repobuild.build(["s"], out, cards_as="alt", collection_label="Alt-arts", ingest=ingest,
                    git_init=False)
    data = json.loads(manifest_path.read_text())
    assert data["packs"][0]["url"] == "https://github.com/hquezser/optcgsim-cards-alt"
    assert data["packs"][0]["label"] == "Alt-arts"   # le label, lui, a bien été rafraîchi


def test_collection_label_and_group_persisted_and_replayed_by_update(tmp_path):
    """update() doit rejouer collection_label/group SANS que l'utilisateur les retape —
    persistés dans .repos-build.json (pas juste passés à build() une fois)."""
    src = tmp_path / "src"
    make_png(src / "Cards" / "OP01" / "OP01-001.png")
    out = tmp_path / "out"
    ingest = lambda s, wd, on_progress=None: src
    repobuild.build(["s"], out, cards_as="alt", collection_label="Alt-arts",
                    collection_group="cards", ingest=ingest, git_init=False)

    log = repobuild.load_build_log(out)
    assert log[0]["collection_label"] == "Alt-arts" and log[0]["collection_group"] == "cards"

    # simule une nouvelle sortie de set + réécrit collection.json comme si un défaut avait
    # effacé label/groupe -> update() doit les régénérer depuis le journal, pas les perdre.
    make_png(src / "Cards" / "OP14" / "OP14-018.png")
    repobuild.update(out, ingest=ingest)
    data = json.loads((out / "collection.json").read_text())
    assert data["packs"][0]["label"] == "Alt-arts"
    assert data["packs"][0]["variant_group"] == "cards"


def test_collection_file_is_visible_not_hidden(tmp_path):
    """Contrairement à .repos-build.json (caché, sources brutes), collection.json est VISIBLE
    -- c'est le manifeste destiné à être publié/partagé (cf. docstring repobuild + P10-c)."""
    src = _minimal_card_src(tmp_path / "src", "OP01-001.png")
    out = tmp_path / "out"
    repobuild.build(["s"], out, cards_as="alt", ingest=lambda s, wd, on_progress=None: src,
                    git_init=False)
    assert (out / "collection.json").exists()
    assert not (out / "collection.json").name.startswith(".")
