"""Tests du point d'entrée CLI — surtout la gestion d'erreur de `main()`.

L'audit a montré que 21 des 25 sous-commandes n'étaient couvertes que par leur logique
métier (via `StudioService`), jamais par leur câblage argparse -> fonction. Ces tests
verrouillent le contrat de SORTIE : un utilisateur ne doit jamais voir de trace Python
brute, et le code de retour doit être exploitable dans un script.
"""

import urllib.error

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
