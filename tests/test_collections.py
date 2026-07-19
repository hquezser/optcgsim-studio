"""Tests du format collection.json (P10-a) : parsing, groupes de variantes, compat de version.

P10 est un chantier en plusieurs volets (voir docs/PLAN-import-packs.md, chantier P10) :
  (a) format + parsing            -> CE FICHIER + studio/assets/collections.py (FAIT)
  (b) génération CLI (repobuild)  -> tests dans test_repobuild.py (FAIT)
  (c) résolution distante + UI    -> FAIT (POST /api/collections/resolve, index.html) ; tests
                                      de la route dans test_api.py (StudioService.resolve_collection)
  (d) tests de bout en bout (c)   -> tests HTTP + erreurs lisibles dans test_api.py
"""

import pytest

from studio.assets import collections


def _manifest(**overrides):
    base = {
        "schema_version": 1,
        "name": "FR classique + full-art",
        "packs": [
            {"family": "translated-fr-classic", "url": "https://github.com/o/fr-classic",
             "label": "Cartes FR classiques", "variant_group": "cards"},
            {"family": "translated-fr-fullart", "url": "https://github.com/o/fr-fullart",
             "label": "Cartes FR alternatives (full-art)", "variant_group": "cards"},
            {"family": "translations-fr", "url": "https://github.com/o/fr-trans",
             "label": "Traduction FR"},
        ],
    }
    base.update(overrides)
    return base


def test_parse_basic_fields():
    col = collections.parse(_manifest())
    assert col.name == "FR classique + full-art"
    assert len(col.packs) == 3
    assert col.packs[0].url == "https://github.com/o/fr-classic"
    assert col.packs[0].family == "translated-fr-classic"


def test_variant_groups_vs_standalone():
    col = collections.parse(_manifest())
    groups = col.variant_groups()
    assert set(groups) == {"cards"}
    assert {p.family for p in groups["cards"]} == {
        "translated-fr-classic", "translated-fr-fullart"}
    standalone = col.standalone_packs()
    assert [p.family for p in standalone] == ["translations-fr"]


def test_label_defaults_to_url_when_missing():
    m = _manifest(packs=[{"url": "https://github.com/o/r"}])
    col = collections.parse(m)
    assert col.packs[0].label == "https://github.com/o/r"


def test_missing_packs_list_raises():
    with pytest.raises(collections.CollectionError, match="packs"):
        collections.parse({"name": "vide"})
    with pytest.raises(collections.CollectionError, match="packs"):
        collections.parse({"name": "vide", "packs": []})


def test_entry_without_url_raises():
    with pytest.raises(collections.CollectionError, match="url"):
        collections.parse(_manifest(packs=[{"label": "sans url"}]))


def test_future_schema_version_warns_not_fails():
    col = collections.parse(_manifest(schema_version=collections.SCHEMA_VERSION + 1))
    assert len(col.packs) == 3   # résolu quand même
    assert col.warnings and "format v" in col.warnings[0]


def test_current_schema_version_no_warning():
    col = collections.parse(_manifest(schema_version=collections.SCHEMA_VERSION))
    assert col.warnings == []


def test_parse_text_and_load(tmp_path):
    import json
    text = json.dumps(_manifest())
    col = collections.parse_text(text)
    assert len(col.packs) == 3

    p = tmp_path / "collection.json"
    p.write_text(text)
    col2 = collections.load(p)
    assert col2.name == col.name


def test_parse_text_invalid_json_raises():
    with pytest.raises(collections.CollectionError, match="JSON invalide"):
        collections.parse_text("{ pas du json valide")


def test_summary_mentions_variant_groups_and_warnings():
    col = collections.parse(_manifest(schema_version=collections.SCHEMA_VERSION + 1))
    s = col.summary()
    assert "3 pack(s)" in s and "cards" in s and "avertissement" in s
