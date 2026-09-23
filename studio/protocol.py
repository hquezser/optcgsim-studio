"""Gestionnaire de protocole `optcgsim://` — l'import en un clic.

La bibliothèque de deckpacks (optcgsim-deckpacks-library) affiche déjà la commande
`studio decks import-pack <url>` à copier-coller. Un schéma d'URL enregistré auprès
de l'OS rend le même geste littéralement cliquable :

    optcgsim://import?url=https%3A%2F%2F<site>%2F<tournoi>%2Fdeckpack.json

Trois morceaux :

1. **Le parseur** (`parse_optcgsim_url`) — strict, car une URL `optcgsim://` cliquée
   depuis une page web ARBITRAIRE est du contenu hostile : le schéma, l'action et la
   cible sont vérifiés, la cible doit être http(s). Tout le reste est rejeté.
2. **La commande** (`studio import-url`) — transforme l'URL en source d'
   `import-pack` : même validation, même écriture, même rapport qu'en CLI.
3. **L'enregistrement** (`studio install-protocol` / `uninstall-protocol`) — par OS :

   - macOS : un applet AppleScript compilé par `osacompile` dans `~/Applications`,
     augmenté de `CFBundleURLTypes` via PlistBuddy puis enregistré auprès de
     LaunchServices. Un binaire scripté ne peut PAS recevoir l'URL (elle arrive par
     Apple Event `GURL`, pas en argv) — d'où l'applet, seul réceptacle minimal.
   - Linux : un `.desktop` dans `~/.local/share/applications` +
     `xdg-mime default … x-scheme-handler/optcgsim`.
   - Windows : `HKCU\\Software\\Classes\\optcgsim` — aucun droit admin requis.

   Chaque fonction prend ses racines/chemins en paramètre : les tests n'écrivent
   jamais dans les vrais dossiers ni ne touchent le vrai registre/LaunchServices.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SCHEME = "optcgsim"
ACTIONS = ("import",)
MAX_URL_LEN = 2048


class ProtocolError(Exception):
    """URL optcgsim:// malformée ou hors contrat — toujours une erreur explicite."""


# --------------------------------------------------------------------------- parsing
def parse_optcgsim_url(raw: str) -> tuple[str, dict[str, str]]:
    """`optcgsim://<action>?<params>` -> (action, params). Lève ProtocolError sinon.

    Forme acceptée — et seulement elle : `optcgsim://import?url=<http(s)://…>`.
    Toute dérive (autre schéma, autre action, `file:`/`javascript:` comme cible,
    espace dans la cible, URL géante) est refusée : ce qui arrive par un clic web
    n'a pas à être accommodant.
    """
    raw = (raw or "").strip()
    if not raw:
        raise ProtocolError("URL vide")
    if len(raw) > MAX_URL_LEN:
        raise ProtocolError(f"URL trop longue ({len(raw)} > {MAX_URL_LEN})")
    try:
        u = urlparse(raw)
    except ValueError as e:
        raise ProtocolError(f"URL illisible : {e}") from e
    if u.scheme.lower() != SCHEME:
        raise ProtocolError(f"schéma « {u.scheme or '?'} » — « {SCHEME} » attendu")

    # `optcgsim://import?…` porte l'action en netloc ; `optcgsim:import?…` ou
    # `optcgsim:///import?…` la porte en chemin. On accepte les trois formes —
    # un seul geste existe, pas besoin de punir sa variante d'écriture.
    action = u.netloc.lower()
    if not action:
        seg = u.path.strip("/")
        action = seg.split("/")[0].lower() if seg else ""
    if action not in ACTIONS:
        raise ProtocolError(
            f"action « {action or '?'} » inconnue — seule « import » existe")

    qs = parse_qs(u.query)
    urls = qs.get("url") or []
    if len(urls) != 1:
        raise ProtocolError("paramètre « url » requis, exactement une fois")
    target = urls[0].strip()
    if not target:
        raise ProtocolError("paramètre « url » vide")
    if len(target) > MAX_URL_LEN:
        raise ProtocolError("URL cible trop longue")
    if any(c in target for c in " \t\r\n\x00"):
        raise ProtocolError("URL cible malformée (espace/contrôle)")
    try:
        t = urlparse(target)
    except ValueError as e:
        raise ProtocolError(f"URL cible illisible : {e}") from e
    if t.scheme.lower() not in ("http", "https") or not t.netloc:
        raise ProtocolError(
            f"cible « {t.scheme or '?'}://… » — seuls http et https sont importables")
    return action, {"url": target}


# --------------------------------------------------------------------------- commande
def studio_command() -> list[str]:
    """La commande qui répond au protocole, figée en chemin absolu à l'installation.

    `studio` sur le PATH d'abord ; à défaut `python -m studio.cli` (même point
    d'entrée, marche aussi en editable install). Jamais de résolution à l'exécution :
    le PATH d'un handler lancé par un navigateur n'est pas celui du terminal.
    """
    exe = shutil.which("studio")
    return [exe] if exe else [sys.executable, "-m", "studio.cli"]


def handler_argv() -> list[str]:
    return studio_command() + ["import-url"]


# --------------------------------------------------------------------------- macOS
_APP_NAME = "optcgsim-import.app"
_LSREGISTER_CANDIDATES = (
    "/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/"
    "LaunchServices.framework/Versions/A/Support/lsregister",
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister",
)


def _as_string(s: str) -> str:
    """Littéral de chaîne AppleScript (échappe \\ et \")."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _applescript_source() -> str:
    cmd = shlex.join(handler_argv())
    return (
        "on open location this_URL\n"
        f"    set rapport to do shell script {_as_string(cmd)} & \" \" "
        "& quoted form of this_URL\n"
        "    display notification rapport with title \"optcgsim-studio\"\n"
        "end open location\n"
    )


def _install_macos(apps_dir: Path | None = None, run=subprocess.run) -> list[str]:
    apps_dir = apps_dir or (Path.home() / "Applications")
    apps_dir.mkdir(parents=True, exist_ok=True)
    app = apps_dir / _APP_NAME
    notes = [f"applet récepteur : {app}"]

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "handler.applescript"
        src.write_text(_applescript_source(), encoding="utf-8")
        r = run(["osacompile", "-o", str(app), str(src)],
                capture_output=True, text=True)
        if r.returncode != 0:
            raise ProtocolError(f"osacompile a échoué : {r.stderr.strip()}")

    plist = app / "Contents" / "Info.plist"
    if plist.is_file():
        # osacompile écrit une plist binaire/XML minimale : on y ajoute la
        # déclaration du schéma et le mode sans-interface, via plistlib plutôt
        # que PlistBuddy (même résultat, testable sans outil externe).
        doc = plistlib.loads(plist.read_bytes())
        doc["CFBundleURLTypes"] = [{
            "CFBundleURLName": "ai.devin.optcgsim.import",
            "CFBundleURLSchemes": [SCHEME],
        }]
        doc["LSBackgroundOnly"] = True
        plist.write_bytes(plistlib.dumps(doc))
    else:  # osacompile sans plist (inattendu) : on la fournit entière
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_bytes(plistlib.dumps({
            "CFBundleIdentifier": "ai.devin.optcgsim.import",
            "CFBundleURLTypes": [{
                "CFBundleURLName": "ai.devin.optcgsim.import",
                "CFBundleURLSchemes": [SCHEME],
            }],
            "LSBackgroundOnly": True,
        }))

    lsregister = next((p for p in _LSREGISTER_CANDIDATES if Path(p).exists()), None)
    if lsregister:
        run([lsregister, "-f", str(app)], capture_output=True, text=True)
        notes.append("schéma enregistré auprès de LaunchServices")
    else:
        # Déclenche l'enregistrement en ouvrant l'applet une fois, en arrière-plan.
        run(["open", "-g", str(app)], capture_output=True, text=True)
        notes.append("lsregister introuvable — applet ouvert une fois pour enregistrer")
    return notes


def _uninstall_macos(apps_dir: Path | None = None, run=subprocess.run) -> list[str]:
    app = (apps_dir or (Path.home() / "Applications")) / _APP_NAME
    notes = []
    lsregister = next((p for p in _LSREGISTER_CANDIDATES if Path(p).exists()), None)
    if lsregister:
        run([lsregister, "-u", str(app)], capture_output=True, text=True)
    if app.exists():
        shutil.rmtree(app)
        notes.append(f"applet supprimé : {app}")
    else:
        notes.append("rien à retirer")
    return notes


# --------------------------------------------------------------------------- Linux
_DESKTOP_NAME = "optcgsim-studio.desktop"


def _exec_field(argv: list[str]) -> str:
    """Valeur `Exec=` d'un .desktop : guillemets doubles uniquement (spec freedesktop)."""
    return " ".join(f'"{a}"' if any(c in a for c in ' "\'\t') else a for a in argv)


def _install_linux(apps_dir: Path | None = None, run=subprocess.run) -> list[str]:
    xdg = os.environ.get("XDG_DATA_HOME")
    apps_dir = apps_dir or (Path(xdg) if xdg else Path.home() / ".local" / "share") / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)
    desk = apps_dir / _DESKTOP_NAME
    desk.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=optcgsim-studio (importeur OPTCGSim)\n"
        f"Exec={_exec_field(handler_argv())} %u\n"
        "MimeType=x-scheme-handler/optcgsim;\n"
        "NoDisplay=true\n"
        "Terminal=true\n",
        encoding="utf-8",
    )
    notes = [f"fichier .desktop : {desk}"]
    if shutil.which("xdg-mime"):
        run(["xdg-mime", "default", _DESKTOP_NAME, f"x-scheme-handler/{SCHEME}"],
            capture_output=True, text=True)
        notes.append("handler enregistré via xdg-mime")
    else:
        notes.append("xdg-mime introuvable — l'association devra être faite à la main")
    if shutil.which("update-desktop-database"):
        run(["update-desktop-database", str(apps_dir)], capture_output=True, text=True)
    return notes


def _uninstall_linux(apps_dir: Path | None = None, run=subprocess.run) -> list[str]:
    xdg = os.environ.get("XDG_DATA_HOME")
    apps_dir = apps_dir or (Path(xdg) if xdg else Path.home() / ".local" / "share") / "applications"
    desk = apps_dir / _DESKTOP_NAME
    if desk.exists():
        desk.unlink()
        if shutil.which("update-desktop-database"):
            run(["update-desktop-database", str(apps_dir)], capture_output=True, text=True)
        return [f".desktop supprimé : {desk}"]
    return ["rien à retirer"]


# --------------------------------------------------------------------------- Windows
def _install_windows(winreg=None) -> list[str]:
    if winreg is None:
        import winreg  # n'existe que sous Windows — import paresseux
    argv = handler_argv()
    cmdline = " ".join(f'"{a}"' for a in argv) + ' "%1"'
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{SCHEME}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:optcgsim Protocol")
        winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(k, r"shell\open\command") as c:
            winreg.SetValueEx(c, "", 0, winreg.REG_SZ, cmdline)
    return [rf"registre : HKCU\Software\Classes\{SCHEME} -> {cmdline}"]


def _uninstall_windows(winreg=None) -> list[str]:
    if winreg is None:
        import winreg
    base = rf"Software\Classes\{SCHEME}"
    for sub in (r"shell\open\command", r"shell\open", r"shell", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, base + (rf"\{sub}" if sub else ""))
        except FileNotFoundError:
            pass
    return [rf"registre : HKCU\Software\Classes\{SCHEME} supprimé"]


# --------------------------------------------------------------------------- dispatch
def install() -> list[str]:
    """Enregistre le schéma auprès de l'OS. Renvoie les actions menées (à afficher)."""
    if sys.platform == "darwin":
        return _install_macos()
    if sys.platform == "win32":
        return _install_windows()
    return _install_linux()


def uninstall() -> list[str]:
    if sys.platform == "darwin":
        return _uninstall_macos()
    if sys.platform == "win32":
        return _uninstall_windows()
    return _uninstall_linux()
