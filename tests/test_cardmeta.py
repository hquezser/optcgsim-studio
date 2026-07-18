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
    # garde-fou : si leaders.json est vide/corrompu, l'import 'leaders only' serait vide
    assert len(cardmeta.leader_ids()) > 50
