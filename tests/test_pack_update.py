"""Tests P3 : sources suivies (update delta + reapply après màj du sim).

Intégration via les fonctions CLI, source LOCALE (offline) : modifier le dossier source
entre `add --follow` et `update` reproduit une mise à jour amont sans réseau.
"""

import struct
import types
import zlib
from pathlib import Path

import pytest

from studio import cli
from studio.assets.manager import AssetManager
from studio.gamepaths import GameInstall
from studio.storage.local import LocalStore


def make_png(path: Path, w: int = 480, h: int = 671) -> Path:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(bytes([w % 256]))) + chunk(b"IEND", b""))
    return path


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fausse install + lib redirigée + DB temp. Renvoie un builder d'args CLI."""
    sa = tmp_path / "app" / "Contents" / "Resources" / "Data" / "StreamingAssets"
    (sa / "Playmats").mkdir(parents=True)
    make_png(sa / "Playmats" / "Blue.png", 1920, 1080)
    make_png(sa / "Playmats" / "Red.png", 1920, 1080)
    monkeypatch.setattr(cli, "_PACK_LIB", tmp_path / "lib")
    # état du manager (backups/manifeste) redirigé lui aussi
    monkeypatch.setattr(cli.AssetManager, "__init__",
                        lambda self, inst, _orig=AssetManager.__init__:
                        _orig(self, inst, state_dir=tmp_path / "mgrstate"))
    db = str(tmp_path / "studio.db")
    app = str(tmp_path / "app")

    def args(**kw):
        base = dict(db=db, app_root=app, name=None, source=None,
                    follow=False, only=None, dry_run=False)
        base.update(kw)
        return types.SimpleNamespace(**base)

    return types.SimpleNamespace(args=args, sa=sa, tmp=tmp_path, db=db, app=app)


def _source(root: Path, blue_w: int) -> Path:
    make_png(root / "Playmats" / "Blue.png", blue_w, 1080)
    return root


def test_update_detects_delta_and_reapplies_when_active(env, capsys):
    src = _source(env.tmp / "src", 2000)
    cli.cmd_packs_add(env.args(source=str(src), name="Theme", follow=True))
    cli.cmd_packs_apply(env.args(name="Theme"))          # appliqué -> Blue du jeu = version pack

    applied = (env.sa / "Playmats" / "Blue.png").read_bytes()
    # « mise à jour amont » : la source change
    make_png(src / "Playmats" / "Blue.png", 2560, 1440)
    capsys.readouterr()
    cli.cmd_packs_update(env.args(name="Theme"))
    out = capsys.readouterr().out
    assert "1 modifié" in out
    assert "ré-appliqué" in out
    # le jeu reflète la NOUVELLE version (pack actif ré-appliqué)
    assert (env.sa / "Playmats" / "Blue.png").read_bytes() != applied


def test_update_no_change_is_noop(env, capsys):
    src = _source(env.tmp / "src", 2000)
    cli.cmd_packs_add(env.args(source=str(src), name="Theme", follow=True))
    capsys.readouterr()
    cli.cmd_packs_update(env.args(name="Theme"))
    assert "déjà à jour" in capsys.readouterr().out


def test_update_skips_unfollowed(env, capsys):
    src = _source(env.tmp / "src", 2000)
    cli.cmd_packs_add(env.args(source=str(src), name="Theme"))   # PAS --follow
    capsys.readouterr()
    cli.cmd_packs_update(env.args(name=None))                    # tous les suivis
    assert "Aucun pack suivi" in capsys.readouterr().out


def test_reapply_after_sim_update(env, capsys):
    src = _source(env.tmp / "src", 2000)
    cli.cmd_packs_add(env.args(source=str(src), name="Theme", follow=True))
    cli.cmd_packs_apply(env.args(name="Theme"))
    # « mise à jour du sim » : le fichier du jeu est écrasé par un nouveau contenu
    make_png(env.sa / "Playmats" / "Blue.png", 1234, 800)
    mgr = AssetManager(env_install(env))
    assert mgr.status()[0]["state"] == "overwritten"
    capsys.readouterr()
    cli.cmd_packs_reapply(env.args())
    out = capsys.readouterr().out
    assert "ré-appliqué" in out
    assert AssetManager(env_install(env)).status()[0]["state"] == "active"


def env_install(env) -> GameInstall:
    from studio.gamepaths import locate
    return locate(Path(env.app))
