"""Tests du stockage offline-first et de la convergence de synchronisation.

Scénario de référence : deux appareils (A = desktop, B = futur mobile), même backend.
"""

import time

import pytest

from studio.storage.local import LocalStore
from studio.storage.remote import FakeRemote
from studio.storage.sync import synchronize


@pytest.fixture()
def device_a(tmp_path):
    with LocalStore(tmp_path / "a" / "studio.db") as s:
        yield s


@pytest.fixture()
def device_b(tmp_path):
    with LocalStore(tmp_path / "b" / "studio.db") as s:
        yield s


def _mk_profile(store) -> dict:
    return store.put("profiles", {"name": "Hugo", "prefs": {"lang": "fr"}})


def _mk_deck(store, profile_id: str, name="P6K", tags=("ranked",)) -> dict:
    return store.put("decks", {
        "profile_id": profile_id, "name": name, "leader": "PRB01-001",
        "cards": {"OP10-005": 4, "ST30-004": 4}, "tags": list(tags),
        "source": "clipboard"})


# ------------------------------------------------------------------ local seul (déconnecté)
def test_local_roundtrip_json_columns(device_a):
    p = _mk_profile(device_a)
    d = _mk_deck(device_a, p["id"])
    got = device_a.get("decks", d["id"])
    assert got["cards"] == {"OP10-005": 4, "ST30-004": 4}   # JSON décodé
    assert got["tags"] == ["ranked"]
    assert got["dirty"] == 1                                 # modif locale non poussée


def test_local_delete_is_tombstone(device_a):
    p = _mk_profile(device_a)
    d = _mk_deck(device_a, p["id"])
    device_a.delete("decks", d["id"])
    assert device_a.list("decks") == []                          # invisible…
    assert device_a.get("decks", d["id"])["deleted"] == 1        # …mais tombstone présent


def test_device_id_stable_across_reopen(tmp_path):
    with LocalStore(tmp_path / "x" / "s.db") as s1:
        did = s1.device_id
    with LocalStore(tmp_path / "x" / "s.db") as s2:
        assert s2.device_id == did


# ------------------------------------------------------------------ convergence 2 appareils
def test_two_devices_converge_through_remote(device_a, device_b):
    remote = FakeRemote()
    p = _mk_profile(device_a)
    d = _mk_deck(device_a, p["id"])

    r1 = synchronize(device_a, remote)
    assert r1["decks"]["pushed"] == 1
    r2 = synchronize(device_b, remote)
    assert r2["decks"]["pulled"] == 1

    got = device_b.get("decks", d["id"])
    assert got["name"] == "P6K" and got["dirty"] == 0        # répliqué, pas re-dirty
    assert device_b.get("profiles", p["id"])["prefs"] == {"lang": "fr"}


def test_last_write_wins_across_devices(device_a, device_b):
    remote = FakeRemote()
    p = _mk_profile(device_a)
    d = _mk_deck(device_a, p["id"])
    synchronize(device_a, remote)
    synchronize(device_b, remote)

    # B renomme (plus récent), A renomme AVANT (plus ancien -> doit perdre)
    a_rec = device_a.get("decks", d["id"])
    a_rec["name"] = "P6K vieux nom"
    device_a.put("decks", a_rec)
    time.sleep(0.02)
    b_rec = device_b.get("decks", d["id"])
    b_rec["name"] = "P6K OP17"
    device_b.put("decks", b_rec)

    synchronize(device_a, remote)      # A pousse sa version (plus ancienne)
    synchronize(device_b, remote)      # B pousse la plus récente -> gagne au serveur
    synchronize(device_a, remote)      # A tire la vérité

    assert device_a.get("decks", d["id"])["name"] == "P6K OP17"
    assert device_b.get("decks", d["id"])["name"] == "P6K OP17"
    assert remote.get("decks", d["id"])["name"] == "P6K OP17"


def test_tombstone_propagates_no_resurrection(device_a, device_b):
    remote = FakeRemote()
    p = _mk_profile(device_a)
    d = _mk_deck(device_a, p["id"])
    synchronize(device_a, remote)
    synchronize(device_b, remote)

    device_b.delete("decks", d["id"])           # suppression sur B
    synchronize(device_b, remote)
    synchronize(device_a, remote)

    assert device_a.list("decks") == []          # supprimé partout
    assert device_a.get("decks", d["id"])["deleted"] == 1
    # un nouveau sync ne ressuscite rien
    synchronize(device_a, remote)
    assert device_a.list("decks") == []


def test_offline_edits_flow_on_next_sync(device_a, device_b):
    remote = FakeRemote()
    p = _mk_profile(device_a)
    synchronize(device_a, remote)
    synchronize(device_b, remote)
    # A travaille hors-ligne : 3 decks
    for i in range(3):
        _mk_deck(device_a, p["id"], name=f"Deck{i}")
    assert len(device_a.dirty_records("decks")) == 3
    synchronize(device_a, remote)
    assert device_a.dirty_records("decks") == []
    synchronize(device_b, remote)
    assert {d["name"] for d in device_b.list("decks")} == {"Deck0", "Deck1", "Deck2"}


def test_unknown_entity_rejected(device_a):
    with pytest.raises(ValueError, match="Entité inconnue"):
        device_a.put("users; DROP TABLE decks", {"name": "x"})
