"""Tests du moteur d'importation de decklists (multi-formats -> natif OPTCGSim)."""

import pytest

from studio.decks.importer import (Decklist, ImportError_, parse_html, parse_text,
                                   scan_persistent_decks, sync_with_store)
from studio.storage.local import LocalStore

# Copie d'une decklist RÉELLE du sim (0Sanji P6K.txt) : 1 leader + 50 cartes.
NATIVE = """1xPRB01-001
4xST30-004
4xOP10-005
3xOP15-012
4xOP12-008
4xPRB02-001
4xOP06-012
4xOP12-002
4xOP15-003
4xST30-005
4xOP08-014
4xEB04-004
3xEB04-007
2xOP09-118
2xOP14-018
"""


def test_native_roundtrip_exact():
    deck = parse_text(NATIVE)
    assert deck.leader == "PRB01-001"
    assert deck.total == 50
    assert deck.cards["ST30-004"] == 4 and deck.cards["OP14-018"] == 2
    # round-trip : re-sérialisé, re-parsé, identique
    again = parse_text(deck.to_native_text())
    assert again.leader == deck.leader and again.cards == deck.cards


@pytest.mark.parametrize("line,cid,qty", [
    ("4x OP01-001", "OP01-001", 4),
    ("4 x OP01-001", "OP01-001", 4),
    ("4 OP01-001", "OP01-001", 4),
    ("OP01-001 x4", "OP01-001", 4),
    ("OP01-001 ×4", "OP01-001", 4),
    ("4x OP01-001 Monkey D. Luffy", "OP01-001", 4),
    ("4 Monkey D. Luffy (OP01-001)", "OP01-001", 4),
    ("- 4x OP01-001", "OP01-001", 4),
    ("4xP-001", "P-001", 4),                      # ids promo
])
def test_community_line_formats(line, cid, qty):
    body = "\n".join(f"4xZZ{i:02d}-001" for i in range(11))   # 44 cartes
    text = f"1xOP01-060\n{line}\n{body}\n2xYY01-001"          # 4 + 44 + 2 = 50
    deck = parse_text(text)
    assert deck.cards[cid] == qty


def test_leader_section_format():
    text = ("Leader:\nOP01-060\n\nDeck:\n"
            + "\n".join(f"4x AA{i:02d}-001" for i in range(12)) + "\n2x BB01-001")
    deck = parse_text(text)
    assert deck.leader == "OP01-060"
    assert deck.total == 50


def test_wrong_size_rejected():
    with pytest.raises(ImportError_, match="49 cartes"):
        parse_text("1xOP01-060\n" + "\n".join(f"4xAA{i:02d}-001" for i in range(12)) + "\n1xBB01-001")


def test_more_than_four_copies_accepted_with_warning():
    # Une liste publiée est présumée légale : on n'échoue pas, on relève (⚠). La carte est
    # inconnue de la table embarquée, donc l'anomalie reste signalée.
    text = ("1xOP01-060\n5xAA01-001\n" + "\n".join(f"4xBB{i:02d}-001" for i in range(11)) + "\n1xCC01-001")
    deck = parse_text(text)
    assert deck.cards["AA01-001"] == 5
    assert deck.total == 50
    assert any("AA01-001" in w and "5 exemplaires" in w for w in deck.warnings)


def test_unlimited_card_beyond_four_accepted_without_warning():
    # OP16-042 « Prisoner of Impel Down » : « you may have any number of this card in your
    # deck ». Deck réel (Green/Blue Luffy, Treasure Cup Utrecht 2026) : 9 exemplaires.
    text = ("1xOP16-022\n9xOP16-042\n" + "\n".join(f"4xBB{i:02d}-001" for i in range(10)) + "\n1xCC01-001")
    deck = parse_text(text)
    assert deck.cards["OP16-042"] == 9
    assert deck.total == 50
    assert deck.warnings == []


def test_validate_resets_warnings_between_calls():
    cards = {"AA01-001": 9, "CC01-001": 1} | {f"BB{i:02d}-001": 4 for i in range(10)}
    deck = Decklist(leader="OP01-060", cards=cards)      # 9 + 1 + 40 = 50
    deck.validate()
    deck.validate()
    assert len(deck.warnings) == 1                       # pas d'accumulation


def test_truncated_import_still_rejected():
    # Le garde-fou réel contre un import corrompu reste le total de 50 cartes.
    with pytest.raises(ImportError_, match="45 cartes"):
        parse_text("1xOP01-060\n9xAA01-001\n" + "\n".join(f"4xBB{i:02d}-001" for i in range(9)))


def test_zero_copies_rejected():
    deck = Decklist(leader="OP01-060", cards={"AA01-001": 0, "BB01-001": 50})
    with pytest.raises(ImportError_, match="0 exemplaires"):
        deck.validate()


def test_no_leader_determinable():
    with pytest.raises(ImportError_, match="Leader indéterminable"):
        parse_text("\n".join(f"4xAA{i:02d}-001" for i in range(12)) + "\n2xB01-001")


def test_empty_text_rejected():
    with pytest.raises(ImportError_, match="Aucune entrée"):
        parse_text("bonjour\npas une decklist\n")


def test_comments_and_noise_ignored():
    deck = parse_text("# mon deck\n// commentaire\n" + NATIVE)
    assert deck.total == 50


# ------------------------------------------------------------------ HTML générique
def test_html_with_embedded_native_export():
    html = f"<html><body><textarea>{NATIVE}</textarea></body></html>"
    deck = parse_html(html)
    assert deck.leader == "PRB01-001" and deck.total == 50


def test_html_generic_pairs_with_dom_noise():
    rows = "".join(
        f'<div class="row"><span class="qty">{q}x</span><span class="id">{cid}</span></div>'
        for cid, q in [("OP01-060", 1)] + [(f"AA{i:02d}-001", 4) for i in range(12)]
        + [("BB01-001", 2)])
    deck = parse_html(f"<html>{rows}</html>")
    assert deck.leader == "OP01-060"
    assert deck.total == 50


def test_html_without_decklist_fails_with_advice():
    with pytest.raises(ImportError_, match="Export OPTCGSim"):
        parse_html("<html><p>Article sur le meta OP17</p></html>")


# ------------------------------------------------------------------ écriture vers le sim
def test_save_to_sim_idempotent_but_no_clobber(tmp_path):
    deck = parse_text(NATIVE)
    p = deck.save_to_sim("Import Test", tmp_path)
    assert p.read_text() == deck.to_native_text()
    deck.save_to_sim("Import Test", tmp_path)          # même contenu : idempotent
    other = Decklist(leader="OP01-060",
                     cards={f"AA{i:02d}-001": 4 for i in range(12)} | {"BB01-001": 2})
    with pytest.raises(ImportError_, match="existe déjà"):
        other.save_to_sim("Import Test", tmp_path)     # contenu différent : refus


# ------------------------------------------------------------------ scan du dossier persistant
def test_scan_persistent_decks_finds_valid_txt(tmp_path):
    (tmp_path / "MonDeck.txt").write_text(NATIVE)
    found = scan_persistent_decks(tmp_path)
    assert len(found) == 1
    assert found[0].name == "MonDeck" and found[0].source == "sim" and found[0].total == 50


def test_scan_persistent_decks_ignores_non_deck_txt(tmp_path):
    (tmp_path / "MonDeck.txt").write_text(NATIVE)
    (tmp_path / "readme.txt").write_text("pas une decklist, juste du texte\n")
    found = scan_persistent_decks(tmp_path)
    assert [d.name for d in found] == ["MonDeck"]


def test_scan_persistent_decks_ignores_subdirectories(tmp_path):
    (tmp_path / "MonDeck.txt").write_text(NATIVE)
    versioned = tmp_path / "1.2.3" / "Cards"
    versioned.mkdir(parents=True)
    (versioned / "cache.txt").write_text(NATIVE)      # même contenu, mais sous-dossier
    found = scan_persistent_decks(tmp_path)
    assert [d.name for d in found] == ["MonDeck"]


def test_scan_persistent_decks_missing_dir_returns_empty(tmp_path):
    assert scan_persistent_decks(tmp_path / "nope") == []


# ------------------------------------------------------------------ sync jeu -> studio
@pytest.fixture()
def store(tmp_path):
    with LocalStore(tmp_path / "studio.db") as s:
        yield s


def test_sync_new_deck_persisted_with_source_sim(tmp_path, store):
    (tmp_path / "DuJeu.txt").write_text(NATIVE)
    r = sync_with_store(tmp_path, store)
    assert r == {"new": ["DuJeu"], "updated": [], "orphaned": []}
    row = store.list("decks")[0]
    assert row["name"] == "DuJeu" and row["source"] == "sim" and row["leader"] == "PRB01-001"


def test_sync_is_additive_second_run_idempotent(tmp_path, store):
    (tmp_path / "DuJeu.txt").write_text(NATIVE)
    sync_with_store(tmp_path, store)
    r = sync_with_store(tmp_path, store)
    assert r == {"new": [], "updated": [], "orphaned": []}
    assert len(store.list("decks")) == 1


def test_sync_updates_changed_deck_preserving_tags(tmp_path, store):
    (tmp_path / "DuJeu.txt").write_text(NATIVE)
    sync_with_store(tmp_path, store)
    row = store.list("decks")[0]
    store.put("decks", {**row, "tags": ["mon-tag"]})   # tag posé côté studio
    # le deck a changé EN JEU (nouveau leader/cartes, mais on garde le même fichier .txt)
    changed = Decklist(leader="OP01-060",
                       cards={f"AA{i:02d}-001": 4 for i in range(12)} | {"BB01-001": 2})
    (tmp_path / "DuJeu.txt").write_text(changed.to_native_text())
    r = sync_with_store(tmp_path, store)
    assert r == {"new": [], "updated": ["DuJeu"], "orphaned": []}
    updated_row = store.get("decks", row["id"])
    assert updated_row["leader"] == "OP01-060" and updated_row["tags"] == ["mon-tag"]


def test_sync_orphaned_deck_reported_not_deleted(tmp_path, store):
    (tmp_path / "DuJeu.txt").write_text(NATIVE)
    sync_with_store(tmp_path, store)
    (tmp_path / "DuJeu.txt").unlink()                 # supprimé côté jeu
    r = sync_with_store(tmp_path, store)
    assert r == {"new": [], "updated": [], "orphaned": ["DuJeu"]}
    assert len(store.list("decks")) == 1               # jamais supprimé en base


def test_sync_ignores_orphans_not_sourced_from_sim(tmp_path, store):
    profiles = store.list("profiles")
    prof = profiles[0] if profiles else store.put("profiles", {"name": "default", "prefs": {}})
    store.put("decks", {"profile_id": prof["id"], "name": "ImporteAilleurs",
                        "leader": "OP01-060", "cards": {"AA00-001": 4}, "tags": [],
                        "source": "ui"})
    r = sync_with_store(tmp_path, store)
    assert r == {"new": [], "updated": [], "orphaned": []}     # pas un deck "sim" -> pas signalé
