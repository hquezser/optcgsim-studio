"""Sélecteur d'emplacements : choisir QUELLE image occupe une case unique du jeu.

Le trou comblé ici, constaté en usage réel : un dépôt d'alt-arts propose 143 DON pour le seul
`Cards/Don/Don.png` du jeu. `apply_mirror` n'écrivant jamais un fichier inconnu du jeu, il
posait celui qui s'appelait littéralement `Don.png` et écartait les 142 autres en silence
(« aucune cible correspondante dans le jeu ») — l'utilisateur n'avait aucun moyen de dire
lequel il voulait.
"""

import json
import struct
import zlib
from pathlib import Path

import pytest

from studio.api.server import StudioService
from studio.assets import slots
from studio.assets.manager import AssetError, AssetManager
from studio.gamepaths import GameInstall


def make_png(path: Path, w: int = 480, h: int = 671, couleur: int = 0) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes([couleur]) * 4)) + chunk(b"IEND", b""))
    return path


def make_jpeg(path: Path, w: int = 1920, h: int = 1080) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    sof = b"\xff\xc0" + struct.pack(">H", 11) + b"\x08" + struct.pack(">HH", h, w) + b"\x01\x11\x00"
    path.write_bytes(b"\xff\xd8" + sof + b"\xff\xd9")
    return path


@pytest.fixture()
def install(tmp_path) -> GameInstall:
    """Une fausse install avec les vrais emplacements : un DON unique, deux tapis, dos, fonds."""
    sa = tmp_path / "app" / "StreamingAssets"
    make_png(sa / "Cards" / "Don" / "Don.png")
    make_png(sa / "Cards" / "OP01" / "OP01-001.png")
    make_png(sa / "Playmats" / "Red.png", 1414, 1000)
    make_png(sa / "Playmats" / "Blue.png", 1414, 1000)
    make_jpeg(sa / "Playmats" / "Playsheet.jpg")       # cohabite mais n'est PAS un tapis
    make_png(sa / "CardBacks" / "CardBackRegular.png")
    make_png(sa / "CardBacks" / "CardBackDon.png")
    make_jpeg(sa / "background.jpg")
    make_jpeg(sa / "deckeditbackground.jpg")
    (tmp_path / "persist").mkdir()
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)


@pytest.fixture()
def mgr(install, tmp_path) -> AssetManager:
    return AssetManager(install, state_dir=tmp_path / "state")


@pytest.fixture()
def lib(tmp_path) -> Path:
    """Bibliothèque façon dépôt d'alt-arts : plusieurs DON et tapis pour une seule case."""
    lib = tmp_path / "lib"
    pack = lib / "altarts"
    for nom in ("Don", "KaidoDon", "ZoroDon"):
        make_png(pack / "Cards" / "Don" / f"{nom}.png", couleur=ord(nom[0]))
    make_png(pack / "Cards" / "OP01" / "OP01-001.png")          # carte : pas un candidat DON
    make_png(pack / "Playmats" / "Red.png", 1414, 1000, couleur=7)
    make_png(pack / "Playmats" / "Zoro.png", 1414, 1000, couleur=9)
    (pack / "manifest.json").write_text("{}", encoding="utf-8")  # bruit : jamais un candidat
    return lib


# ------------------------------------------------------------------ inventaire des emplacements
def test_slots_reflect_the_install(install):
    ids = [s.id for s in slots.list_slots(install)]
    assert ids == ["don", "cardback:regular", "cardback:don",
                   "background:main", "background:deck_editor",
                   "playmat:Blue", "playmat:Red"]


def test_playsheet_jpg_is_not_a_playmat_slot(install):
    """`Playsheet.jpg` vit dans Playmats/ mais n'est pas un tapis — et `apply_playmat` exige
    un PNG : le proposer produirait un emplacement impossible à remplir."""
    assert "playmat:Playsheet" not in {s.id for s in slots.list_slots(install)}


def test_slot_absent_from_install_is_not_listed(tmp_path):
    """Un emplacement n'existe que si son fichier existe : même règle que `apply_mirror`,
    donc tout emplacement listé est toujours applicable."""
    sa = tmp_path / "app" / "StreamingAssets"
    make_png(sa / "Playmats" / "Red.png", 1414, 1000)
    inst = GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)
    assert [s.id for s in slots.list_slots(inst)] == ["playmat:Red"]


# ------------------------------------------------------------------ candidats
def test_candidates_are_the_images_the_apply_would_have_dropped(lib):
    noms = sorted(c["name"] for c in slots.candidates(lib, "don"))
    assert noms == ["Don", "KaidoDon", "ZoroDon"]


def test_candidates_exclude_other_categories_and_non_images(lib):
    assert [c["name"] for c in slots.candidates(lib, "playmats")] == ["Red", "Zoro"]
    assert "manifest" not in {c["name"] for c in slots.candidates(lib, "playmats")}
    # une carte ordinaire n'est candidate à aucun emplacement
    for kind in ("don", "playmats", "cardbacks", "backgrounds"):
        assert "OP01-001" not in {c["name"] for c in slots.candidates(lib, kind)}


def test_candidate_counts_matches_candidates(lib):
    counts = slots.candidate_counts(lib)
    assert counts["don"] == 3 and counts["playmats"] == 2
    for kind, n in counts.items():
        assert len(slots.candidates(lib, kind)) == n


def test_resolve_candidate_refuses_escaping_the_library(lib, tmp_path):
    """`pack`/`rel` viennent d'une requête HTTP : sans confinement, `rel=../../…` ferait
    copier un fichier arbitraire DANS LE JEU."""
    secret = tmp_path / "secret.png"
    make_png(secret)
    with pytest.raises(AssetError):
        slots.resolve_candidate(lib, "altarts", "../../secret.png")
    with pytest.raises(AssetError):
        slots.resolve_candidate(lib, "..", "secret.png")


def test_resolve_candidate_refuses_non_images(lib):
    with pytest.raises(AssetError):
        slots.resolve_candidate(lib, "altarts", "manifest.json")


# ------------------------------------------------------------------ application d'un choix
def test_choosing_a_don_replaces_the_single_game_file(install, mgr, lib):
    cible = install.cards_dir / "Don" / "Don.png"
    origine = cible.read_bytes()
    choix = lib / "altarts" / "Cards" / "Don" / "KaidoDon.png"

    slots.apply_choice(mgr, slots.find_slot(install, "don"), choix)

    assert cible.read_bytes() == choix.read_bytes() != origine


def test_choosing_a_playmat_targets_the_named_slot_not_the_file_name(install, mgr, lib):
    """Le cœur du sélecteur : une image nommée « Zoro.png » atterrit dans le tapis « Red »
    parce que l'utilisateur l'a choisi — là où l'application d'un pack ne pouvait poser
    « Zoro.png » nulle part, faute de cible portant ce nom."""
    choix = lib / "altarts" / "Playmats" / "Zoro.png"
    slots.apply_choice(mgr, slots.find_slot(install, "playmat:Red"), choix)

    assert (install.playmats_dir / "Red.png").read_bytes() == choix.read_bytes()
    assert (install.playmats_dir / "Blue.png").read_bytes() != choix.read_bytes()


def test_choice_is_reversible_to_the_pristine_original(install, mgr, lib):
    cible = install.playmats_dir / "Red.png"
    origine = cible.read_bytes()
    slots.apply_choice(mgr, slots.find_slot(install, "playmat:Red"),
                       lib / "altarts" / "Playmats" / "Zoro.png")
    mgr.restore(cible)
    assert cible.read_bytes() == origine


def test_wrong_format_is_refused_not_written(install, mgr, tmp_path):
    """Un tapis est un PNG : poser un JPEG doit échouer PROPREMENT, sans rien écrire."""
    cible = install.playmats_dir / "Red.png"
    origine = cible.read_bytes()
    with pytest.raises(AssetError):
        slots.apply_choice(mgr, slots.find_slot(install, "playmat:Red"),
                           make_jpeg(tmp_path / "pas-un-tapis.jpg"))
    assert cible.read_bytes() == origine


def test_choice_origin_is_not_a_pack(install, mgr, lib):
    """Un choix n'appartient à aucun pack : retirer le pack d'où venait l'image ne doit pas
    le défaire silencieusement (`restore_source('pack:…')` ne doit pas le voir)."""
    slots.apply_choice(mgr, slots.find_slot(install, "don"),
                       lib / "altarts" / "Cards" / "Don" / "KaidoDon.png")
    cible = str((install.cards_dir / "Don" / "Don.png").resolve())
    assert mgr._manifest[cible]["source"] == "slot:don"


# ------------------------------------------------------------------ persistance du choix
def test_choices_survive_a_reload(tmp_path):
    store = slots.SlotChoices(tmp_path / "state")
    store.set("don", Path("/x/KaidoDon.png"), "altarts", "Cards/Don/KaidoDon.png")
    assert slots.SlotChoices(tmp_path / "state").all()["don"]["pack"] == "altarts"
    store.clear("don")
    assert slots.SlotChoices(tmp_path / "state").all() == {}


def test_corrupt_choice_file_is_not_fatal(tmp_path):
    """Le fichier peut être tronqué par un arrêt brutal : l'UI doit s'ouvrir quand même."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "slots.json").write_text("{ tronq", encoding="utf-8")
    assert slots.SlotChoices(tmp_path / "state").all() == {}


# ------------------------------------------------------------------ intégration service/API
@pytest.fixture()
def svc(install, tmp_path, lib) -> StudioService:
    return StudioService(install, db_path=str(tmp_path / "studio.db"),
                         lib_dir=lib, state_dir=tmp_path / "state")


def test_service_describes_slots_with_candidate_counts(svc):
    par_id = {s["id"]: s for s in svc.slots()}
    assert par_id["don"]["candidates"] == 3
    assert par_id["playmat:Red"]["candidates"] == 2
    assert par_id["don"]["swapped"] is False and par_id["don"]["choice"] is None


def test_service_choose_then_reset(svc, install):
    cible = install.cards_dir / "Don" / "Don.png"
    origine = cible.read_bytes()

    r = svc.choose_slot("don", "altarts", "Cards/Don/ZoroDon.png")
    assert r["targets"] and cible.read_bytes() != origine
    par_id = {s["id"]: s for s in svc.slots()}
    assert par_id["don"]["choice"] == {"pack": "altarts", "rel": "Cards/Don/ZoroDon.png",
                                       "missing": False}

    assert svc.reset_slot("don")["restored"] is True
    assert cible.read_bytes() == origine
    assert {s["id"]: s for s in svc.slots()}["don"]["choice"] is None


def test_reset_on_an_untouched_slot_is_not_an_error(svc):
    assert svc.reset_slot("playmat:Blue")["restored"] is False


def test_unknown_slot_is_a_keyerror(svc):
    with pytest.raises(KeyError):
        svc.slot_candidates("playmat:Inexistant")


def test_applying_a_pack_does_not_silently_undo_a_choice(svc, install, tmp_path):
    """Régression : sans ré-application, « Appliquer » reposait le `Don.png` du pack
    par-dessus le DON choisi à la main — en silence, exactement la panne qu'on corrige."""
    svc.choose_slot("don", "altarts", "Cards/Don/ZoroDon.png")
    choisi = (install.cards_dir / "Don" / "Don.png").read_bytes()

    # un pack qui contient SON propre Don.png (celui qui « gagnait » avant)
    src = tmp_path / "src"
    make_png(src / "Cards" / "Don" / "Don.png", couleur=255)
    make_png(src / "Cards" / "OP01" / "OP01-001.png", couleur=255)
    svc.add_source(str(src), name="Theme")
    rep = svc.apply("Theme")

    assert "Cards/Don/Don.png" in rep["applied"]
    assert rep["reapplied_slots"] == ["DON!!"]
    assert (install.cards_dir / "Don" / "Don.png").read_bytes() == choisi
    # le reste du pack s'applique normalement
    assert (install.cards_dir / "OP01" / "OP01-001.png").read_bytes() != b""


def test_apply_does_not_rewrite_slots_the_pack_never_touched(svc, install, tmp_path):
    """On ne ré-applique QUE ce que le pack vient d'écraser : sinon chaque `apply` réécrirait
    tous les emplacements choisis (des écritures dans un bundle signé, pour rien)."""
    svc.choose_slot("playmat:Red", "altarts", "Playmats/Zoro.png")
    src = tmp_path / "src2"
    make_png(src / "Cards" / "OP01" / "OP01-001.png", couleur=42)
    svc.add_source(str(src), name="CardsOnly")
    assert svc.apply("CardsOnly")["reapplied_slots"] == []


def test_dry_run_never_reapplies(svc, tmp_path):
    svc.choose_slot("don", "altarts", "Cards/Don/ZoroDon.png")
    src = tmp_path / "src3"
    make_png(src / "Cards" / "Don" / "Don.png", couleur=255)
    svc.add_source(str(src), name="Dry")
    assert svc.apply("Dry", dry_run=True)["reapplied_slots"] == []


def test_choice_pointing_at_a_removed_pack_is_flagged_not_crashing(svc, install, lib):
    """Le pack d'où venait l'image peut être retiré : le jeu garde l'image (déjà écrite),
    l'UI le signale, et rien ne lève."""
    svc.choose_slot("don", "altarts", "Cards/Don/ZoroDon.png")
    (lib / "altarts" / "Cards" / "Don" / "ZoroDon.png").unlink()
    assert {s["id"]: s for s in svc.slots()}["don"]["choice"]["missing"] is True


def test_candidate_image_path_is_confined_to_the_library(svc):
    with pytest.raises(AssetError):
        svc.candidate_image("altarts", "../../../../etc/passwd")


# ------------------------------------------------------------------ cohérence import/apply
def test_don_category_is_the_same_at_import_and_at_apply():
    """Régression : `mirror_category` ignorait la catégorie « don » et rangeait
    `Cards/Don/…` dans « cards ». Cocher « don » à l'import marchait, filtrer sur « don »
    à l'application ne rendait jamais rien. Une seule fonction classe désormais."""
    from studio.assets import packlib
    for rel in ("Cards/Don/Don.png", "Cards/OP01/OP01-001.png", "Playmats/Blue.png",
                "CardBacks/CardBackDon.png", "background.jpg"):
        assert AssetManager.mirror_category(rel) == packlib.classify_rel(rel)[0]
    assert AssetManager.mirror_category("Cards/Don/Don.png") == "don"


def test_apply_only_don_filters_on_the_don_category(mgr, tmp_path, install):
    pack = tmp_path / "p"
    make_png(pack / "Cards" / "Don" / "Don.png", couleur=3)
    make_png(pack / "Cards" / "OP01" / "OP01-001.png", couleur=3)
    rep = mgr.apply_mirror(pack, origin="pack:X", only={"don"})
    assert rep["applied"] == ["Cards/Don/Don.png"]
    assert rep["filtered"] == ["Cards/OP01/OP01-001.png"]


def test_a_pack_containing_only_cards_keeps_its_mirror_root(install, tmp_path):
    """Régression (trouvée en écrivant le sélecteur) : `add_pack` descendait dans l'unique
    sous-dossier d'une source pour défaire l'emballage des zips GitHub — y compris quand ce
    dossier était `Cards/`, la racine miroir elle-même. Les cartes s'en tiraient par chance
    (leur id se relit dans le nom de fichier) mais `Cards/Don/Don.png` devenait
    `Don/Don.png` : ni chemin miroir, ni id de carte, donc « non classé » et jamais copié.
    Un pack d'alt-arts ne contient très souvent QUE `Cards/`."""
    from studio.assets import packlib

    src = tmp_path / "altarts-only-cards"
    make_png(src / "Cards" / "Don" / "Don.png", couleur=1)
    make_png(src / "Cards" / "OP01" / "OP01-001.png", couleur=1)
    _, rep = packlib.add_pack(str(src), install, name="P", lib_dir=tmp_path / "lib2",
                              work_dir=tmp_path / "w")
    assert rep.unclassified == []
    assert "Cards/Don/Don.png" in rep.files


# ── L'invariant vaut sur TOUS les chemins de code, pas seulement dans l'interface web ──────

def test_un_choix_survit_a_une_application_de_pack(install, mgr, lib):
    """`reapply_choices` repose le choix par-dessus le pack qui vient de l'écraser.

    C'est la raison d'être du module : sans ça, « appliquer » remet le `Don.png` du pack
    par-dessus le DON choisi à la main, en silence.
    """
    choisi = lib / "altarts" / "Cards" / "Don" / "KaidoDon.png"
    slot = slots.find_slot(install, "don")
    slots.apply_choice(mgr, slot, choisi)
    slots.SlotChoices(mgr.state_dir).set("don", choisi, "altarts", "Cards/Don/KaidoDon.png")
    cible = install.cards_dir / "Don" / "Don.png"
    assert cible.read_bytes() == choisi.read_bytes()

    # Un pack repose SON Don.png par-dessus.
    pack = lib / "autre"
    intrus = make_png(pack / "Cards" / "Don" / "Don.png", couleur=99)
    rep = mgr.apply_mirror(pack, origin="pack:autre")
    assert cible.read_bytes() == intrus.read_bytes(), "le pack devrait avoir écrasé le choix"

    redone = slots.reapply_choices(install, mgr, rep["applied"])
    assert redone == [slot.label]
    assert cible.read_bytes() == choisi.read_bytes(), "le choix n'a pas été réimposé"


def test_le_cli_reimpose_les_choix_comme_le_serveur(install, mgr, lib, monkeypatch, capsys):
    """Le défaut trouvé en relisant ce lot : `_reapply_choices` vivait dans le SERVEUR, et
    les quatre points d'application du CLI ne l'appelaient pas. Le sélecteur tenait donc
    dans l'interface web et pas en ligne de commande — même famille de défaut que la
    divergence `classify_rel` que ce même lot corrige, où l'import et l'application
    classaient les chemins avec deux fonctions qui ne disaient plus la même chose.
    """
    from studio import cli

    choisi = lib / "altarts" / "Cards" / "Don" / "ZoroDon.png"
    slots.apply_choice(mgr, slots.find_slot(install, "don"), choisi)
    slots.SlotChoices(mgr.state_dir).set("don", choisi, "altarts", "Cards/Don/ZoroDon.png")

    pack = lib / "encore"
    make_png(pack / "Cards" / "Don" / "Don.png", couleur=77)
    rep = mgr.apply_mirror(pack, origin="pack:encore")
    cible = install.cards_dir / "Don" / "Don.png"
    assert cible.read_bytes() != choisi.read_bytes()

    cli._reimposer_choix(mgr, install, rep["applied"])
    assert cible.read_bytes() == choisi.read_bytes()
    assert "réimposés" in capsys.readouterr().out


def test_sans_choix_le_cli_ne_dit_rien(install, mgr, lib, capsys):
    """Une ligne à chaque application n'informerait plus de rien : on ne parle que si ça a
    effectivement joué.
    """
    from studio import cli

    pack = lib / "vierge"
    make_png(pack / "Cards" / "Don" / "Don.png", couleur=5)
    rep = mgr.apply_mirror(pack, origin="pack:vierge")
    cli._reimposer_choix(mgr, install, rep["applied"])
    assert "réimposés" not in capsys.readouterr().out


def test_tous_les_points_d_application_du_cli_reimposent_les_choix():
    """Garde-fou structurel : c'est l'OUBLI d'un chemin qui a créé le défaut, pas sa logique.

    Chaque appel à `apply_mirror` dans le CLI doit être accompagné d'une réimposition. Un
    test de comportement ne peut pas couvrir un cinquième point d'application ajouté plus
    tard ; ce contrôle-là, oui.
    """
    import re

    src = (Path(__file__).resolve().parent.parent / "studio" / "cli.py").read_text(
        encoding="utf-8")
    fonctions = re.split(r"\ndef ", src)
    manquants = [f.split("(")[0] for f in fonctions
                 if "apply_mirror(" in f and "_reimposer_choix(" not in f]
    assert not manquants, (
        f"ces fonctions appliquent un pack sans réimposer les choix d'emplacement : "
        f"{manquants}")
