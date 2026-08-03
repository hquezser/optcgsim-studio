"""Tests de cardmeta.is_leader (table de leaders vendorisée)."""

from studio.assets import cardmeta


def test_known_leaders_recognized():
    # ids présents dans leaders.json (vérifiés à l'extraction : card_type == "Leader")
    assert cardmeta.is_leader("OP01-001")
    assert cardmeta.is_leader("PRB01-001")     # Sanji alt (leader de l'utilisateur)


def test_characters_are_not_leaders():
    assert not cardmeta.is_leader("OP01-016")  # Nami, Character
    assert not cardmeta.is_leader("OP14-018")  # event/character


def test_unknown_id_is_not_leader():
    assert not cardmeta.is_leader("ZZ99-999")


def test_table_is_non_trivial():
    # garde-fou : si card_types.json est vide/corrompu, l'import 'leaders only' serait vide
    assert len(cardmeta.leader_ids()) > 50


# ------------------------------------------------------------------ P8 : types
def test_card_type_values():
    assert cardmeta.card_type("OP01-001") == "Leader"
    assert cardmeta.card_type("OP14-018") == "Event"
    assert cardmeta.card_type("ZZ99-999") is None


def test_ids_of_type_case_insensitive_and_disjoint():
    ev = cardmeta.ids_of_type("event")
    ld = cardmeta.ids_of_type("Leader")
    assert len(ev) > 100 and len(ld) > 50
    assert ev.isdisjoint(ld)                 # un id a un seul type
    assert "OP14-018" in ev and "OP01-001" in ld


# ------------------------------------------- cartes sans limite de copies
def test_unlimited_cards_recognized():
    # les seules cartes portant « any number of this card in your deck »
    assert cardmeta.is_unlimited("OP16-042")   # Prisoner of Impel Down
    assert cardmeta.is_unlimited("OP01-075")   # Pacifista
    assert cardmeta.is_unlimited("OP08-072")   # Biscuit Warrior


def test_ordinary_cards_are_limited():
    assert not cardmeta.is_unlimited("OP01-016")   # Nami
    assert not cardmeta.is_unlimited("ZZ99-999")   # inconnue → défaut prudent


def test_unlimited_table_stays_small():
    # garde-fou : une table qui enflerait signalerait une extraction trop large
    assert 0 < len(cardmeta.unlimited_ids()) <= 10
