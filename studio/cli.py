"""CLI du studio : assets (cosmétiques), decks (import), sync (cloud)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .assets.manager import AssetError, AssetManager
from .decks import importer
from .gamepaths import locate
from .storage.local import DEFAULT_DB, LocalStore


def _install(args):
    inst = locate(Path(args.app_root) if args.app_root else None)
    if inst is None:
        raise SystemExit("Installation OPTCGSim introuvable — préciser --app-root")
    return inst


# --------------------------------------------------------------------------- assets
def cmd_assets_inventory(args) -> int:
    print(json.dumps(AssetManager(_install(args)).inventory(),
                     indent=1, ensure_ascii=False))
    return 0


def cmd_assets_apply_pack(args) -> int:
    mgr = AssetManager(_install(args))
    counts = mgr.apply_pack(Path(args.pack))
    print("Appliqué :", ", ".join(f"{k}={v}" for k, v in counts.items() if v))
    print("`studio assets restore-all` restaure les originaux à tout moment.")
    return 0


def cmd_assets_apply_mirror(args) -> int:
    mgr = AssetManager(_install(args))
    rep = mgr.apply_mirror(Path(args.pack), dry_run=args.dry_run)
    verb = "seraient remplacés" if args.dry_run else "remplacés"
    print(f"Racine détectée : {rep['root']}")
    print(f"{len(rep['applied'])} fichiers {verb}.")
    if rep["skipped_txt"]:
        print(f"  {len(rep['skipped_txt'])} traduction(s) ignorée(s) du miroir — "
              f"utiliser `studio assets` (fusion) : {rep['skipped_txt']}")
    if rep["ignored"]:
        print(f"  {len(rep['ignored'])} ignoré(s) (pas de cible / format / non-image) :")
        for i in rep["ignored"][:10]:
            print(f"    - {i['path']} : {i['reason']}")
        if len(rep["ignored"]) > 10:
            print(f"    … et {len(rep['ignored']) - 10} de plus")
    if not args.dry_run and rep["applied"]:
        print("Ferme le sim avant/pendant. `studio assets restore-all` annule tout.")
    return 0


def cmd_assets_status(args) -> int:
    rows = AssetManager(_install(args)).status()
    if not rows:
        print("Aucun swap actif.")
        return 0
    for r in rows:
        print(f"[{r['state']:<11}] {Path(r['target']).name:<28} <- {r['source']}")
    return 0


def cmd_assets_restore_all(args) -> int:
    n = AssetManager(_install(args)).restore_all()
    print(f"{n} fichiers restaurés à l'original.")
    return 0


# --------------------------------------------------------------------------- decks
def cmd_decks_import(args) -> int:
    if args.clipboard:
        deck = importer.from_clipboard(name=args.name)
    elif args.url:
        deck = importer.from_url(args.url, name=args.name)
    elif args.file:
        deck = importer.parse_text(Path(args.file).read_text(errors="ignore"),
                                   name=args.name, source=args.file)
    else:
        raise SystemExit("Préciser la source : --clipboard, --url ou --file")
    name = args.name or f"Imported {deck.leader}"

    inst = _install(args)
    path = deck.save_to_sim(name, inst.persistent)
    with LocalStore(Path(args.db)) as store:
        profiles = store.list("profiles")
        profile = profiles[0] if profiles else store.put(
            "profiles", {"name": "default", "prefs": {}})
        store.put("decks", {
            "profile_id": profile["id"], "name": name, "leader": deck.leader,
            "cards": deck.cards, "tags": args.tags.split(",") if args.tags else [],
            "source": deck.source})
    print(f"Deck « {name} » ({deck.leader}, {deck.total} cartes)")
    print(f"  -> sim  : {path}")
    print(f"  -> base : {args.db} (synchronisable)")
    return 0


def cmd_decks_list(args) -> int:
    with LocalStore(Path(args.db)) as store:
        for d in store.list("decks"):
            tags = ",".join(d["tags"]) or "-"
            print(f"{d['name']:<28} {d['leader']:<12} tags:{tags:<20} "
                  f"({'dirty' if d['dirty'] else 'synced'})")
    return 0


# --------------------------------------------------------------------------- sync
def cmd_sync(args) -> int:
    if not args.url or not args.token:
        print("Mode DÉCONNECTÉ (SQLite local uniquement).\n"
              "Pour activer la synchronisation : studio sync --url <api> --token <jeton>\n"
              "Contrat serveur : voir studio/storage/remote.py (REST ou Supabase).")
        return 0
    from .storage.remote import RestRemote
    from .storage.sync import synchronize
    with LocalStore(Path(args.db)) as store:
        report = synchronize(store, RestRemote(args.url, args.token))
    for entity, r in report.items():
        print(f"{entity:<16} poussés={r['pushed']} tirés={r['pulled']} "
              f"conservés(LWW)={r['skipped']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="studio",
                                description="OPTCGSim Studio — QoL, cosmétiques, decks, sync.")
    p.add_argument("--db", default=str(DEFAULT_DB), help="base SQLite du studio")
    p.add_argument("--app-root", default=None, help="chemin de l'app OPTCGSim (sinon auto)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("assets", help="cosmétiques (inventaire, packs, restauration)")
    sa = pa.add_subparsers(dest="sub", required=True)
    sa.add_parser("inventory").set_defaults(func=cmd_assets_inventory)
    ap = sa.add_parser("apply-pack")
    ap.add_argument("pack", help="dossier du pack (layout drag&drop)")
    ap.set_defaults(func=cmd_assets_apply_pack)
    am = sa.add_parser("apply-mirror",
                       help="pack calqué sur StreamingAssets (Themer & assimilés)")
    am.add_argument("pack", help="dossier/zip décompressé du thème")
    am.add_argument("--dry-run", action="store_true", help="analyser sans rien écrire")
    am.set_defaults(func=cmd_assets_apply_mirror)
    sa.add_parser("status").set_defaults(func=cmd_assets_status)
    sa.add_parser("restore-all").set_defaults(func=cmd_assets_restore_all)

    pd = sub.add_parser("decks", help="importation et gestion de decklists")
    sd = pd.add_subparsers(dest="sub", required=True)
    di = sd.add_parser("import")
    di.add_argument("--clipboard", action="store_true")
    di.add_argument("--url", default=None)
    di.add_argument("--file", default=None)
    di.add_argument("--name", default=None)
    di.add_argument("--tags", default=None, help="tags séparés par des virgules")
    di.set_defaults(func=cmd_decks_import)
    sd.add_parser("list").set_defaults(func=cmd_decks_list)

    ps = sub.add_parser("sync", help="synchronisation cloud (mode connecté)")
    ps.add_argument("--url", default=None)
    ps.add_argument("--token", default=None)
    ps.set_defaults(func=cmd_sync)
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except (AssetError, importer.ImportError_) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
