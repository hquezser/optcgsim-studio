"""Tests du gestionnaire `optcgsim://` — studio/protocol.py.

Le parseur est testé EN PREMIER par l'attaque : cette URL arrive d'un clic sur une
page web arbitraire, c'est le contenu le plus hostile du lot. Les installs sont
testés avec racines et exécuteurs injectés — jamais dans les vrais dossiers, jamais
dans le vrai registre, jamais auprès du vrai LaunchServices.
"""
from __future__ import annotations

import plistlib
import shutil
import sys
import types
from pathlib import Path
from urllib.parse import quote

import pytest

from studio import cli, protocol


# --------------------------------------------------------------------------- parsing
def test_parse_import_ok():
    url = "optcgsim://import?url=" + quote("https://ex.fr/t/deckpack.json", safe="")
    assert protocol.parse_optcgsim_url(url) == ("import", {"url": "https://ex.fr/t/deckpack.json"})


def test_parse_url_non_encodee_aussi_acceptee():
    url = "optcgsim://import?url=https://ex.fr/t/deckpack.json"
    assert protocol.parse_optcgsim_url(url)[1]["url"] == "https://ex.fr/t/deckpack.json"


@pytest.mark.parametrize("forme", [
    "optcgsim:import?url=https://ex.fr/p.json",     # sans //
    "optcgsim:///import?url=https://ex.fr/p.json",  # netloc vide
    "OPTCGSIM://IMPORT?url=https://ex.fr/p.json",   # casse
])
def test_parse_formes_alternatives(forme):
    assert protocol.parse_optcgsim_url(forme)[0] == "import"


@pytest.mark.parametrize("mauvaise", [
    "", "   ",
    "https://ex.fr/p.json",                       # pas le schéma
    "optcgsim://",                                # pas d'action
    "optcgsim://nimportequoi?url=https://ex.fr",  # action inconnue
    "optcgsim://import",                          # url absente
    "optcgsim://import?url=",                     # url vide
    "optcgsim://import?url=https://a.fr&url=https://b.fr",  # deux fois
    "optcgsim://import?url=ftp://ex.fr/p.json",   # schéma cible non http
    "optcgsim://import?url=file:///etc/passwd",   # file: interdit
    "optcgsim://import?url=javascript:alert(1)",  # javascript: interdit
    "optcgsim://import?url=https://ex.fr%20mal",  # espace encodé -> malformée
    "optcgsim://import?url=https://",             # netloc vide
    "optcgsim://import?url=" + "https://ex.fr/" + "x" * 3000,  # trop longue
])
def test_parse_refuse(mauvaise):
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_optcgsim_url(mauvaise)


def test_parse_url_trop_longue_rejetee():
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_optcgsim_url("optcgsim://import?url=" + "x" * 3000)


# --------------------------------------------------------------------------- commande
def test_studio_command_absolu():
    cmd = protocol.studio_command()
    attendu = shutil.which("studio")
    assert cmd == ([attendu] if attendu else [sys.executable, "-m", "studio.cli"])
    assert protocol.handler_argv()[-1] == "import-url"


def test_applescript_contient_do_shell_script_et_import_url():
    src = protocol._applescript_source()
    assert "on open location" in src and "do shell script" in src
    assert "import-url" in src and "quoted form of this_URL" in src


# --------------------------------------------------------------------------- install Linux
def test_install_linux_ecrit_desktop_et_enregistre(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(protocol.shutil, "which",
                        lambda name: "/usr/bin/" + name if name in ("xdg-mime", "studio") else None)
    monkeypatch.setattr(protocol, "studio_command", lambda: ["/usr/bin/studio"])
    notes = protocol._install_linux(apps_dir=tmp_path, run=lambda *a, **k: calls.append(a[0]) or
                                    types.SimpleNamespace(returncode=0, stderr=""))
    desk = tmp_path / "optcgsim-studio.desktop"
    body = desk.read_text()
    assert "x-scheme-handler/optcgsim" in body and "import-url %u" in body
    assert ["xdg-mime", "default", "optcgsim-studio.desktop", "x-scheme-handler/optcgsim"] in calls
    assert any(".desktop" in n for n in notes)


def test_install_linux_sans_xdg_mime_avertit(tmp_path, monkeypatch):
    monkeypatch.setattr(protocol.shutil, "which", lambda name: None)
    monkeypatch.setattr(protocol, "studio_command", lambda: ["/usr/bin/studio"])
    notes = protocol._install_linux(apps_dir=tmp_path,
                                    run=lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert any("xdg-mime introuvable" in n for n in notes)


def test_uninstall_linux_retire_le_fichier(tmp_path, monkeypatch):
    (tmp_path / "optcgsim-studio.desktop").write_text("x")
    monkeypatch.setattr(protocol.shutil, "which", lambda name: None)
    notes = protocol._uninstall_linux(apps_dir=tmp_path,
                                      run=lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert not (tmp_path / "optcgsim-studio.desktop").exists()
    assert any("supprimé" in n for n in notes)


# --------------------------------------------------------------------------- install macOS
def test_install_macos_applet_plist_et_lsregister(tmp_path, monkeypatch):
    faux_ls = tmp_path / "lsregister"  # un vrai fichier : Path(p).exists() suffit
    faux_ls.write_text("")
    calls = []
    monkeypatch.setattr(protocol, "_LSREGISTER_CANDIDATES", (str(faux_ls),))

    def faux_run(cmd, **kw):
        calls.append(cmd)
        if cmd[0] == "osacompile":
            app = Path(cmd[cmd.index("-o") + 1])
            (app / "Contents" / "MacOS").mkdir(parents=True)
            (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleName": "x"}))
        return types.SimpleNamespace(returncode=0, stderr="")

    notes = protocol._install_macos(apps_dir=tmp_path, run=faux_run)
    plist = plistlib.loads((tmp_path / "optcgsim-import.app" / "Contents" / "Info.plist").read_bytes())
    assert plist["CFBundleURLTypes"][0]["CFBundleURLSchemes"] == ["optcgsim"]
    assert plist["LSBackgroundOnly"] is True
    assert calls[0][0] == "osacompile" and calls[-1][:2] == [str(faux_ls), "-f"]
    assert any("LaunchServices" in n for n in notes)


def test_uninstall_macos_retire_l_applet(tmp_path, monkeypatch):
    app = tmp_path / "optcgsim-import.app"
    app.mkdir()
    monkeypatch.setattr(protocol, "_LSREGISTER_CANDIDATES", ())
    notes = protocol._uninstall_macos(apps_dir=tmp_path,
                                      run=lambda *a, **k: types.SimpleNamespace(returncode=0))
    assert not app.exists() and any("supprimé" in n for n in notes)


@pytest.mark.skipif(shutil.which("osacompile") is None, reason="osacompile absent (pas macOS)")
def test_osacompile_compile_reellement_le_script(tmp_path):
    """Le script généré COMPILE — sinon install-protocol produit un applet cassé."""
    import subprocess
    src = tmp_path / "h.applescript"
    src.write_text(protocol._applescript_source())
    r = subprocess.run(["osacompile", "-o", str(tmp_path / "a.app"), str(src)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "a.app" / "Contents" / "Info.plist").is_file()


# --------------------------------------------------------------------------- install Windows
def test_install_windows_registre_hkcu():
    ecrit = {}

    class FakeKey:
        def __init__(self, name): self.name = name
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def set_value(k, name, _r, _t, v):
        ecrit.setdefault(k.name, {})[name or "(défaut)"] = v

    def create_key(parent, path):
        plein = path if isinstance(parent, str) else f"{parent.name}\\{path}"
        return FakeKey(plein)

    faux = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        REG_SZ=1,
        CreateKey=create_key,
        SetValueEx=set_value,
    )
    notes = protocol._install_windows(winreg=faux)
    assert ecrit[rf"Software\Classes\{protocol.SCHEME}"]["URL Protocol"] == ""
    cmdline = ecrit[rf"Software\Classes\{protocol.SCHEME}\shell\open\command"]["(défaut)"]
    assert "import-url" in cmdline and '"%1"' in cmdline
    assert "registre" in notes[0]


def test_uninstall_windows_supprime_les_cles():
    suppr = []
    faux = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        DeleteKey=lambda hive, path: suppr.append(path),
    )
    protocol._uninstall_windows(winreg=faux)
    assert suppr[-1] == rf"Software\Classes\{protocol.SCHEME}"
    assert len(suppr) == 4


# --------------------------------------------------------------------------- dispatch CLI
def test_import_url_dispatch_vers_import_pack(monkeypatch):
    recu = {}

    class Args:
        url = "optcgsim://import?url=" + quote("https://ex.fr/p/deckpack.json", safe="")

    monkeypatch.setattr(cli, "cmd_decks_import_pack", lambda a: recu.update(src=a.source) or 0)
    assert cli.cmd_import_url(Args()) == 0
    assert recu["src"] == "https://ex.fr/p/deckpack.json"


def test_import_url_mauvaise_url_leve_protocolerror():
    with pytest.raises(protocol.ProtocolError):
        cli.cmd_import_url(types.SimpleNamespace(url="optcgsim://import?url=file:///x"))


def test_main_renvoie_1_sur_protocolerror(capsys):
    code = cli.main(["import-url", "optcgsim://import?url=javascript:alert(1)"])
    assert code == 1
    assert "Erreur" in capsys.readouterr().err
