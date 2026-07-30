"""Fuzzing des parseurs exposés à du contenu TIERS.

Ces quatre fonctions sont les portes d'entrée du contenu venant d'inconnus : une decklist
collée, une page web, les noms de fichiers d'un `.zip` communautaire, un `collection.json`
distant. Le contrat n'est pas « ne jamais échouer » — c'est **échouer proprement** : une
erreur du domaine (`ImportError_`, `CollectionError`) que l'appelant sait présenter, jamais
une exception inattendue qui remonte en trace brute jusqu'à l'utilisateur ou fait tomber un
job de fond.

Graine fixe : reproductible. Échantillon volontairement modeste pour rester sous la seconde ;
une campagne plus large (~10 000 cas) a été passée à la main, également sans exception
inattendue.
"""

from __future__ import annotations

import random

from studio.assets import collections, packlib
from studio.decks import importer

GRAINE = 1234

MORCEAUX = [
    "1xOP01-001", "4xOP01-002", "", "\x00", "\n", "\r\n", "Leader:", "x" * 5000,
    "999999999xOP01-001", "-1xOP01-001", "1x", "x1", "0xOP01-001", "🏴‍☠️",
    "<script>", "../../etc/passwd", "1xOP01-001\x00", "1x" + "A" * 300 + "-001",
    "‮OP01-001",                                  # marque d'inversion de sens d'écriture
    "1xOP01-001;DROP TABLE decks", " " * 1000, "\t1xOP01-001",
]

SEGMENTS_CHEMIN = ["Cards", "..", "", "OP01", ".git", "a" * 300, "\x00", "Playmats",
                   "x.png", "CON", "..\\..", "%2e%2e"]

MANIFESTES = [
    {}, {"packs": None}, {"packs": []}, {"packs": [None]}, {"packs": [[]]},
    {"packs": [{"url": None}]}, {"packs": [{"url": "x" * 5000}]},
    {"packs": [{"url": "u", "variant_group": {"a": 1}}]},
    {"name": {"x": 1}, "packs": [{"url": "u"}]},
    {"packs": [{"url": "u"}], "schema_version": "abc"},
    {"packs": [{"url": "u", "label": ["liste"]}]},
    {"packs": {"pas": "une liste"}},
]


def _verifie(fn, cas, tolerees):
    """Appelle `fn` sur chaque cas ; ne tolère que les exceptions du domaine."""
    surprises = []
    for c in cas:
        try:
            fn(c)
        except tolerees:
            pass
        except Exception as e:                      # noqa: BLE001 — c'est l'objet du test
            surprises.append((repr(c)[:80], f"{type(e).__name__}: {e}"))
    assert not surprises, f"exception(s) inattendue(s) : {surprises[:5]}"


def test_parse_text_echoue_toujours_proprement():
    rnd = random.Random(GRAINE)
    cas = ["\n".join(rnd.choices(MORCEAUX, k=rnd.randint(0, 8))) for _ in range(600)]
    _verifie(lambda c: importer.parse_text(c, name="fuzz"), cas, importer.ImportError_)


def test_parse_html_echoue_toujours_proprement():
    rnd = random.Random(GRAINE)
    # Le corps est assemblé HORS f-string : un antislash dans une expression de f-string
    # n'est légal qu'à partir de Python 3.12 (PEP 701), or on supporte 3.10.
    corps = ["\n".join(rnd.choices(MORCEAUX, k=rnd.randint(0, 6))) for _ in range(400)]
    cas = [f"<html><body>{c}</body></html>" for c in corps]
    _verifie(lambda c: importer.parse_html(c, name="fuzz"), cas, importer.ImportError_)


def test_classify_rel_ne_leve_jamais():
    """Appelée sur CHAQUE entrée d'une archive : une exception ici ferait tomber tout
    l'import à cause d'un seul nom de fichier tordu."""
    rnd = random.Random(GRAINE)
    cas = ["/".join(rnd.choices(SEGMENTS_CHEMIN, k=rnd.randint(1, 6))) for _ in range(600)]
    _verifie(packlib.classify_rel, cas, ())


def test_collection_parse_echoue_toujours_proprement():
    rnd = random.Random(GRAINE)
    cas = [rnd.choice(MANIFESTES) for _ in range(400)]
    _verifie(collections.parse, cas, collections.CollectionError)
