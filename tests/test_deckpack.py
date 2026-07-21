"""Tests du pack de decks (P6) : résolution multi-decks, jamais bloquante."""

import json
from pathlib import Path

import pytest

from studio.decks import deckpack, importer

# decklist réelle valide (50 cartes + leader), réutilisée inline.
VALID = ("1xPRB01-001\n4xST30-004\n4xOP10-005\n3xOP15-012\n4xOP12-008\n4xPRB02-001\n"
         "4xOP06-012\n4xOP12-002\n4xOP15-003\n4xST30-005\n4xOP08-014\n4xEB04-004\n"
         "3xEB04-007\n2xOP09-118\n2xOP14-018\n")


def test_resolve_inline_text_and_file(tmp_path):
    (tmp_path / "decks").mkdir()
    (tmp_path / "decks" / "z.txt").write_text(VALID)
    manifest = {"name": "Meta OP16", "author": "Trecore", "decks": [
        {"name": "Sanji", "tags": ["meta", "op16"], "text": VALID},
        {"name": "Zoro", "tags": ["rogue"], "file": "decks/z.txt"},
    ]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert rep.name == "Meta OP16" and rep.author == "Trecore"
    assert len(rep.imported) == 2 and rep.failed == []
    assert rep.imported[0].name == "Sanji" and rep.imported[0].tags == ["meta", "op16"]
    assert rep.imported[0].deck.leader == "PRB01-001"


def test_resolve_source_url_injected(tmp_path):
    calls = {}
    def fake_from_url(url, name):
        calls["url"] = url
        return importer.parse_text(VALID, name=name)
    manifest = {"name": "P", "decks": [
        {"name": "Kid", "tags": ["meta"], "source_url": "https://site/kid"}]}
    rep = deckpack.resolve(manifest, tmp_path, from_url=fake_from_url)
    assert calls["url"] == "https://site/kid"
    assert rep.imported[0].deck.total == 50


def test_one_bad_deck_does_not_block_others(tmp_path):
    manifest = {"name": "P", "decks": [
        {"name": "Bon", "text": VALID},
        {"name": "Cassé", "text": "1xOP01-060\n4xAA01-001"},   # 5 cartes -> invalide
        {"name": "SansContenu"},                                # ni text/file/url
    ]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert [d.name for d in rep.imported] == ["Bon"]
    failed = {f["name"] for f in rep.failed}
    assert failed == {"Cassé", "SansContenu"}
    assert all(f["reason"] for f in rep.failed)


def test_duplicate_names_disambiguated(tmp_path):
    manifest = {"name": "P", "decks": [
        {"name": "Sanji", "text": VALID}, {"name": "Sanji", "text": VALID}]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert [d.name for d in rep.imported] == ["Sanji", "Sanji (2)"]


def test_file_path_traversal_rejected(tmp_path):
    (tmp_path / "secret.txt").write_text(VALID)
    manifest = {"name": "P", "decks": [{"name": "evil", "file": "../secret.txt"}]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert rep.imported == [] and "illégal" in rep.failed[0]["reason"]


def test_empty_or_missing_decks_raises(tmp_path):
    with pytest.raises(deckpack.DeckPackError, match="decks"):
        deckpack.resolve({"name": "P"}, tmp_path)


def test_find_manifest_root_and_wrapped(tmp_path):
    (tmp_path / "deckpack.json").write_text("{}")
    assert deckpack.find_manifest(tmp_path).name == "deckpack.json"
    wrapped = tmp_path / "w"
    (wrapped / "inner").mkdir(parents=True)
    (wrapped / "inner" / "deckpack.json").write_text("{}")
    assert deckpack.find_manifest(wrapped).parent.name == "inner"


def test_find_manifest_absent_raises(tmp_path):
    with pytest.raises(deckpack.DeckPackError, match="introuvable"):
        deckpack.find_manifest(tmp_path)


def test_schema_version_future_warns_not_fails(tmp_path):
    manifest = {"name": "P", "schema_version": deckpack.SCHEMA_VERSION + 1,
                "decks": [{"name": "S", "text": VALID}]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert len(rep.imported) == 1                 # résolu quand même
    assert rep.warnings and "format v" in rep.warnings[0]


def test_current_schema_version_no_warning(tmp_path):
    manifest = {"name": "P", "schema_version": deckpack.SCHEMA_VERSION,
                "decks": [{"name": "S", "text": VALID}]}
    rep = deckpack.resolve(manifest, tmp_path)
    assert rep.warnings == []


def test_resolve_sets_source_to_pack_name_not_internal_file_or_url(tmp_path):
    (tmp_path / "decks").mkdir()
    (tmp_path / "decks" / "z.txt").write_text(VALID)
    manifest = {"name": "Meta OP16", "decks": [
        {"name": "Sanji", "text": VALID},
        {"name": "Zoro", "file": "decks/z.txt"},
        {"name": "Kid", "source_url": "https://site/kid"},
    ]}
    rep = deckpack.resolve(manifest, tmp_path,
                          from_url=lambda url, name: importer.parse_text(VALID, name=name))
    assert {d.deck.source for d in rep.imported} == {"deckpack:Meta OP16"}


def test_generate_is_inverse_of_resolve(tmp_path):
    manifest = {"name": "Meta OP16", "decks": [
        {"name": "Sanji", "tags": ["meta", "op16"], "text": VALID}]}
    rep = deckpack.resolve(manifest, tmp_path)
    generated = deckpack.generate("Meta OP16", rep.imported, author="Trecore")
    assert generated["name"] == "Meta OP16" and generated["author"] == "Trecore"
    assert generated["schema_version"] == deckpack.SCHEMA_VERSION
    assert generated["decks"] == [{"name": "Sanji", "tags": ["meta", "op16"],
                                   "text": rep.imported[0].deck.to_native_text()}]
    # round-trip : re-résoudre le pack généré reproduit le même deck/tags
    rep2 = deckpack.resolve(generated, tmp_path)
    assert rep2.imported[0].name == "Sanji" and rep2.imported[0].tags == ["meta", "op16"]
    assert rep2.imported[0].deck.leader == rep.imported[0].deck.leader
    assert rep2.imported[0].deck.cards == rep.imported[0].deck.cards


def test_from_source_ingests_and_cleans(tmp_path):
    # ingest factice : renvoie un dossier contenant un deckpack.json
    content = tmp_path / "content"
    content.mkdir()
    (content / "deckpack.json").write_text(json.dumps(
        {"name": "P", "decks": [{"name": "S", "text": VALID}]}))
    work = tmp_path / "work"
    def fake_ingest(source, wd):
        return content
    rep = deckpack.from_source("whatever", fake_ingest, work)
    assert rep.imported[0].name == "S"
