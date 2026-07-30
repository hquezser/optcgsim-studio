"""P14.1 — garde-fous statiques sur l'UI : le contenu tiers ne doit jamais être interpolé brut.

Le projet n'a pas de harnais JS (choix délibéré : zéro Node, zéro build). Ces tests lisent donc
`index.html` comme un texte et verrouillent les deux motifs qui ont réellement causé le XSS
stocké : un `onclick` construit par interpolation, et un champ tiers inséré sans `esc()`.

Vérifié en vrai navigateur par ailleurs : un `deckpack.json` dont le deck s'appelle
`<img src=x onerror="window.__XSS_MARKER=1">` s'affiche en texte littéral et n'exécute rien.
"""

import re
from pathlib import Path

import pytest

INDEX = Path(__file__).parent.parent / "studio" / "api" / "static" / "index.html"


@pytest.fixture(scope="module")
def html() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def code(html) -> str:
    """`html` sans les commentaires de ligne JS — sinon un commentaire qui CITE le motif
    interdit (pour expliquer pourquoi il l'est) déclencherait les tests ci-dessous."""
    return "\n".join(re.sub(r"^\s*//.*$", "", line) for line in html.splitlines())


def _html_templates(code: str) -> list[str]:
    """Littéraux de gabarit qui produisent du HTML (donc contenant une balise ouvrante).

    C'est le seul contexte où l'échappement est requis : un gabarit qui finit dans
    `textContent` (toasts, barre de progression) ou dans un libellé de job — lui-même
    ré-échappé à l'affichage — n'est pas un sink HTML.
    """
    return [m.group(0) for m in re.finditer(r"`[^`]*`", code, re.S)
            if re.search(r"<[a-zA-Z/]", m.group(0))]


def test_no_handler_attribute_is_built_by_interpolation(code):
    """`onclick="f('<nom>')"` remet le nom dans un parseur JS — la faille d'origine.

    Les actions passent par `data-*` + délégation : le nom ne traverse plus jamais de code.
    """
    offenders = [m.group(0) for m in re.finditer(r'on\w+\s*=\s*"[^"]*\$\{', code)]
    assert offenders == [], (
        "Attribut de gestionnaire construit par interpolation — utiliser data-* + "
        f"addEventListener : {offenders}")


# Champs qui viennent d'une source TIERCE : zip communautaire, deckpack.json, collection.json
# distante. Chacun doit être interpolé via esc(), jamais brut.
UNTRUSTED = [
    "p.name", "p.kind", "p.applied", "p.label", "p.url",      # packs et collections
    "d.name", "d.leader", "d.id", "d.deck",                   # decks
    "col.name", "group", "w",                                 # collections
    "j.label",                                                # jobs (libellé = nom de source)
    "e.message",                                              # erreurs (peuvent porter un nom)
]


@pytest.mark.parametrize("field", UNTRUSTED)
def test_untrusted_field_is_never_interpolated_raw_into_html(code, field):
    raw = "${" + field + "}"
    offenders = [t[:120] for t in _html_templates(code) if raw in t]
    assert offenders == [], (
        f"`{raw}` interpolé brut dans un gabarit HTML — envelopper dans esc() : "
        f"`${{esc({field})}}`. Gabarit(s) fautif(s) : {offenders}")


def test_esc_helper_exists_and_covers_the_five_dangerous_characters(html):
    """`esc` doit couvrir & < > \" ' — l'apostrophe comprise (« Luffy's alt art »)."""
    i = html.find("const esc =")
    assert i != -1, "helper esc() absent de index.html"
    body = html[i:i + 400]
    for ch, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                       ('"', "&quot;"), ("'", "&#39;")):
        assert entity in body, f"esc() n'échappe pas {ch!r} (attendu {entity})"


def test_toast_uses_textcontent_not_innerhtml(html):
    """Les toasts affichent des noms tiers ; ils doivent rester en textContent."""
    m = re.search(r"function toast\(.*?\n\}", html, re.S)
    assert m, "fonction toast() introuvable"
    assert ".textContent" in m.group(0)
    assert ".innerHTML" not in m.group(0)
