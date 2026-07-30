"""Tests du point d'entrée CLI — surtout la gestion d'erreur de `main()`.

L'audit a montré que 21 des 25 sous-commandes n'étaient couvertes que par leur logique
métier (via `StudioService`), jamais par leur câblage argparse -> fonction. Ces tests
verrouillent le contrat de SORTIE : un utilisateur ne doit jamais voir de trace Python
brute, et le code de retour doit être exploitable dans un script.
"""

import urllib.error
from pathlib import Path

from studio import cli

# Note : `build_parser()` est appelé DANS `main()`, donc après le monkeypatch — le
# `set_defaults(func=...)` capture bien la fonction remplacée.


# --------------------------------------------------------- P15.8 : erreurs réseau et disque
def test_url_error_is_readable_not_a_traceback(monkeypatch, capsys):
    """`decks import --url`, `sync`, `packs add` sortaient une trace brute sur une simple
    coupure réseau — `main()` n'attrapait pas `URLError`."""
    def boum(args):
        raise urllib.error.URLError("Name or service not known")
    monkeypatch.setattr(cli, "cmd_assets_status", boum)

    code = cli.main(["assets", "status"])

    assert code == 1
    err = capsys.readouterr().err
    assert "Erreur réseau" in err
    assert "Traceback" not in err


def test_os_error_is_readable_not_a_traceback(monkeypatch, capsys):
    """Disque plein, dossier du jeu non écrivable, chemin inexistant."""
    def boum(args):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cli, "cmd_assets_status", boum)

    code = cli.main(["assets", "status"])

    assert code == 1
    err = capsys.readouterr().err
    assert "Erreur système" in err
    assert "Traceback" not in err


def test_keyboard_interrupt_exits_130_quietly(monkeypatch, capsys):
    """Ctrl-C pendant un téléchargement : message court, code 130 (convention shell)."""
    def boum(args):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "cmd_assets_status", boum)

    code = cli.main(["assets", "status"])

    assert code == 130
    assert "Traceback" not in capsys.readouterr().err


def test_cert_error_adds_the_macos_hint(monkeypatch, capsys):
    """Sur macOS/python.org, l'erreur de certificat a un correctif connu : la CLI doit le
    donner, comme le fait déjà l'UI."""
    import ssl
    def boum(args):
        raise urllib.error.URLError(ssl.SSLCertVerificationError(
            "certificate verify failed: unable to get local issuer certificate"))
    monkeypatch.setattr(cli, "cmd_assets_status", boum)

    code = cli.main(["assets", "status"])

    assert code == 1
    assert "Install Certificates" in capsys.readouterr().err


def test_successful_command_returns_zero(monkeypatch):
    monkeypatch.setattr(cli, "cmd_assets_status", lambda args: 0)
    assert cli.main(["assets", "status"]) == 0


# ------------------------------------------------------------------ P19.2 : studio doctor
def _png_valide(chemin: Path, w: int = 480, h: int = 671) -> Path:
    import struct
    import zlib

    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_bytes(b"\x89PNG\r\n\x1a\n"
                       + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                       + ch(b"IDAT", zlib.compress(b"\x00" * 12)) + ch(b"IEND", b""))
    return chemin


def _install_factice(tmp_path):
    """Fausse installation complète : jamais la vraie (garde-fou du projet)."""
    from studio.gamepaths import GameInstall

    sa = tmp_path / "app" / "StreamingAssets"
    (sa / "Playmats").mkdir(parents=True)
    _png_valide(sa / "Cards" / "OP01" / "OP01-001.png")
    (tmp_path / "persist").mkdir(exist_ok=True)
    return GameInstall(app_root=tmp_path / "app", streaming_assets=sa,
                       persistent=tmp_path / "persist", os_name="test", verified=True)


def _doctor(tmp_path, monkeypatch, capsys, inst=None):
    """Lance `studio doctor` contre une install, une base et un état FACTICES."""
    inst = inst or _install_factice(tmp_path)
    monkeypatch.setattr(cli, "locate", lambda *a, **k: inst)
    code = cli.main(["--db", str(tmp_path / "studio.db"), "doctor",
                     "--state-dir", str(tmp_path / "etat")])
    return code, capsys.readouterr().out, inst


def test_doctor_reports_healthy_install(tmp_path, monkeypatch, capsys):
    code, sortie, _ = _doctor(tmp_path, monkeypatch, capsys)
    assert code == 0
    assert "Aucun problème bloquant" in sortie
    assert "✗" not in sortie


def test_doctor_flags_a_missing_backup_as_blocking(tmp_path, monkeypatch, capsys):
    """LE cas critique : une sauvegarde d'origine disparue signifie qu'un fichier du jeu ne
    pourra plus jamais être restauré. L'utilisateur doit l'apprendre par un diagnostic, pas
    en découvrant que `restore-all` échoue le jour où il en a besoin.
    """
    from studio.assets.manager import AssetManager
    inst = _install_factice(tmp_path)
    etat = tmp_path / "etat"
    mgr = AssetManager(inst, state_dir=etat)
    source = tmp_path / "remplacement.png"
    _png_valide(source)
    mgr.apply_card("OP01-001", source, "pack:Test")

    # la sauvegarde disparaît (nettoyage accidentel de ~/.optcgsim-studio/backups)
    sauvegarde = Path(next(iter(mgr._manifest.values()))["backup"])
    sauvegarde.unlink()

    code, sortie, _ = _doctor(tmp_path, monkeypatch, capsys, inst=inst)

    assert code == 1, "une sauvegarde manquante doit être signalée comme bloquante"
    assert "Sauvegardes d'origine" in sortie
    assert "1 manquante(s)" in sortie
    assert "OP01-001.png" in sortie, "le diagnostic doit NOMMER le fichier concerné"


def test_doctor_flags_unreadable_records(tmp_path, monkeypatch, capsys):
    """Un enregistrement corrompu est sauté silencieusement à la lecture (P15.9) — c'est le
    bon comportement pour l'UI, mais le diagnostic, lui, doit le dire."""
    from studio.storage.local import LocalStore
    db = tmp_path / "studio.db"
    with LocalStore(db) as s:
        prof = s.put("profiles", {"name": "H", "prefs": {}})
        deck = s.put("decks", {"profile_id": prof["id"], "name": "D", "leader": "OP01-001",
                               "cards": {"OP01-002": 4}, "tags": []})
        s.conn.execute("UPDATE decks SET cards='{casse' WHERE id=?", (deck["id"],))
        s.conn.commit()

    code, sortie, _ = _doctor(tmp_path, monkeypatch, capsys)

    assert code == 1
    assert "Enregistrements illisibles" in sortie


def test_doctor_never_writes_anything(tmp_path, monkeypatch, capsys):
    """Un diagnostic qui modifie l'état n'est pas un diagnostic."""
    _, _, inst = _doctor(tmp_path, monkeypatch, capsys)
    empreinte = lambda: {p: p.stat().st_mtime_ns
                         for p in inst.streaming_assets.rglob("*") if p.is_file()}
    avant = empreinte()
    _doctor(tmp_path, monkeypatch, capsys, inst=inst)
    assert empreinte() == avant, "doctor ne doit modifier aucun fichier du jeu"


# ------------------- P16.4 : une seule source de vérité pour la persistance d'un deck
def test_cli_and_api_persist_a_deck_identically(tmp_path):
    """La CLI et l'API doivent produire la MÊME ligne en base et le MÊME .txt.

    La séquence existait en trois exemplaires et avait déjà dérivé (l'isolation des échecs
    d'écriture n'avait été ajoutée qu'à deux d'entre eux). Ce test échouerait si une copie
    réapparaissait et divergeait.
    """
    from studio.api.server import StudioService
    from studio.decks import importer
    from studio.storage.local import LocalStore

    deck50 = ("1xOP01-060\n" + "\n".join(f"4xAA{i:02d}-001" for i in range(12))
              + "\n2xBB01-001")

    # côté API
    inst_api = _install_factice(tmp_path / "api")
    svc = StudioService(inst_api, db_path=str(tmp_path / "api.db"),
                        lib_dir=tmp_path / "lib", state_dir=tmp_path / "etat_api")
    svc.import_deck(text=deck50, name="MonDeck", tags=["meta"])
    par_api = svc.decks()[0]

    # côté CLI (même helper partagé)
    inst_cli = _install_factice(tmp_path / "cli")
    from studio.decks import persist
    with LocalStore(tmp_path / "cli.db") as store:
        deck = importer.parse_text(deck50, name="MonDeck", source="ui")
        chemin = persist.persist_deck(store, deck, "MonDeck", ["meta"], inst_cli.persistent)
        par_cli = store.list("decks")[0]

    comparable = lambda d: {k: d[k] for k in ("name", "leader", "cards", "tags", "source")}
    assert comparable(par_api) == comparable(par_cli)
    assert chemin.read_text() == (inst_api.persistent / "MonDeck.txt").read_text()


def test_persist_deck_creates_the_default_profile_once(tmp_path):
    from studio.decks import importer, persist
    from studio.storage.local import LocalStore

    inst = _install_factice(tmp_path)
    deck50 = ("1xOP01-060\n" + "\n".join(f"4xAA{i:02d}-001" for i in range(12))
              + "\n2xBB01-001")
    with LocalStore(tmp_path / "db.sqlite") as store:
        for n in ("A", "B", "C"):
            persist.persist_deck(store, importer.parse_text(deck50, name=n), n, [],
                                 inst.persistent)
        assert len(store.list("profiles")) == 1, "un seul profil par défaut"
        assert len(store.list("decks")) == 3
