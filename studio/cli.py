"""CLI du studio : assets (cosmétiques), decks (import), sync (cloud)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path

from .assets import packlib
from .assets.manager import AssetError, AssetManager
from .decks import importer, persist
from .gamepaths import locate
from .nettls import CERT_FIX_HINT, is_cert_error
from .storage.local import DEFAULT_DB, LocalStore

_PHASE_LABEL = {"download": "téléchargement", "extract": "extraction",
               "classify": "analyse", "copy": "copie", "apply": "application"}


def _console_progress(phase: str, done: int, total: int) -> None:
    """Callback de progression pour le terminal : une ligne, écrasée en place.

    Les téléchargements de dossiers communautaires complets peuvent peser plusieurs
    centaines de Mo et prendre plusieurs minutes — sans repère visuel, ça ressemble à un
    blocage. `total == 0` (taille inconnue) affiche un compteur simple."""
    label = _PHASE_LABEL.get(phase, phase)
    if phase == "download":
        mb = f"{done // (1<<20)} Mo" + (f"/{total // (1<<20)} Mo" if total else "")
        pct = f" ({100 * done / total:.0f}%)" if total else ""
        print(f"\r  {label}… {mb}{pct}", end="", flush=True)
    elif total:
        print(f"\r  {label}… {done}/{total} ({100 * done / total:.0f}%)", end="", flush=True)
    else:
        print(f"\r  {label}… {done}", end="", flush=True)
    if total and done >= total:
        print()


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


def cmd_doctor(args) -> int:
    """Diagnostic de l'installation — la première chose à joindre à un rapport de bug.

    Ne modifie RIEN : que des lectures. Sort en 1 s'il existe au moins un problème
    bloquant (✗), 0 sinon (les ⚠ sont informatifs).
    """
    import platform
    import sqlite3

    from .config import Config

    # `--state-dir` permet de diagnostiquer une autre installation du studio (ou une copie
    # de sauvegarde) sans toucher à la sienne — et rend la commande testable sans approcher
    # le vrai ~/.optcgsim-studio.
    etat = Path(args.state_dir) if getattr(args, "state_dir", None) else None

    lignes: list[tuple[str, str, str]] = []      # (niveau, sujet, détail)

    def ok(sujet, detail=""):
        lignes.append(("✓", sujet, detail))

    def attention(sujet, detail=""):
        lignes.append(("⚠", sujet, detail))

    def probleme(sujet, detail=""):
        lignes.append(("✗", sujet, detail))

    # -- environnement
    v = sys.version_info
    ok("Python", f"{v.major}.{v.minor}.{v.micro} ({platform.system()} {platform.machine()})")
    if (v.major, v.minor) < (3, 10):
        probleme("Version de Python", "3.10 minimum requis")

    # -- installation du jeu
    inst = locate(Path(args.app_root) if args.app_root else None)
    if inst is None:
        probleme("Installation OPTCGSim", "introuvable — préciser --app-root")
    else:
        ok("Installation", f"{inst.os_name} — {inst.app_root}")
        if not inst.verified:
            attention("Cartographie des chemins",
                      f"non confirmée sur {inst.os_name} (chemins Unity supposés)")
        for label, chemin in (("StreamingAssets", inst.streaming_assets),
                              ("Données utilisateur", inst.persistent)):
            if not chemin.exists():
                probleme(label, f"absent : {chemin}")
            elif not os.access(chemin, os.W_OK):
                probleme(label, f"non écrivable : {chemin}  "
                                "(le studio n'élève JAMAIS ses privilèges)")
            else:
                ok(label, str(chemin))

    # -- état du studio : sauvegardes et manifeste (le cœur de la réversibilité)
    if inst is not None:
        mgr = AssetManager(inst, state_dir=etat) if etat else AssetManager(inst)
        entrees = mgr._manifest
        manquantes = [k for k, e in entrees.items() if not Path(e["backup"]).exists()]
        if manquantes:
            probleme("Sauvegardes d'origine",
                     f"{len(manquantes)} manquante(s) sur {len(entrees)} — "
                     "ces fichiers ne pourront PAS être restaurés")
            for k in manquantes[:5]:
                lignes.append(("", "", f"      {Path(k).name}"))
        else:
            ok("Sauvegardes d'origine",
               f"{len(entrees)} entrée(s), toutes présentes"
               if entrees else "aucun swap actif")
        if entrees:
            etats: dict[str, int] = {}
            for r in mgr.status():
                etats[r["state"]] = etats.get(r["state"], 0) + 1
            detail = ", ".join(f"{n} {e}" for e, n in sorted(etats.items()))
            (attention if etats.get("overwritten") or etats.get("missing") else ok)(
                "État des swaps", detail
                + ("  (« overwritten » = mise à jour du sim ou modification externe)"
                   if etats.get("overwritten") else ""))

    # -- base de données
    db = Path(args.db)
    if not db.exists():
        attention("Base de données", f"pas encore créée ({db})")
    else:
        try:
            with LocalStore(db) as store:
                comptes = {e: len(store.list(e))
                           for e in ("profiles", "decks", "cosmetic_packs")}
                ok("Base de données",
                   ", ".join(f"{n} {e}" for e, n in comptes.items()) + f"  ({db})")
                if store.corrupt:
                    probleme("Enregistrements illisibles",
                             f"{len(store.corrupt)} — sautés à la lecture")
                    for c in store.corrupt[:5]:
                        lignes.append(("", "", f"      {c}"))
        except sqlite3.DatabaseError as e:
            probleme("Base de données", f"illisible : {e}")

    # -- configuration
    cfg = Config(etat) if etat else Config()
    ok("Token GitHub", "configuré (dépôts privés accessibles)"
       if cfg.has_github_token() else "absent (dépôts publics uniquement)")
    src = cfg.default_collection_source()
    ok("Collection par défaut", src or "aucune")

    largeur = max(len(s) for _, s, _ in lignes if s) if lignes else 0
    print("Diagnostic optcgsim-studio\n" + "─" * 60)
    for niveau, sujet, detail in lignes:
        if not sujet:
            print(detail)
        else:
            print(f"{niveau} {sujet.ljust(largeur)}  {detail}")
    problemes = sum(1 for n, _, _ in lignes if n == "✗")
    print("─" * 60)
    if problemes:
        print(f"{problemes} problème(s) bloquant(s). "
              "Joins cette sortie à ton rapport de bug.")
        return 1
    print("Aucun problème bloquant.")
    return 0


def cmd_assets_apply_pack(args) -> int:
    mgr = AssetManager(_install(args))
    rep = mgr.apply_pack(Path(args.pack))
    print("Appliqué :", ", ".join(f"{k}={v}" for k, v in rep["applied"].items() if v))
    for s in rep["skipped"]:
        print(f"  ✗ {s['category']}/{s['name']} : {s['reason']}")
    print("`studio assets restore-all` restaure les originaux à tout moment.")
    # Code de sortie honnête : un pack partiellement appliqué n'est pas un succès.
    return 1 if rep["skipped"] else 0


def cmd_assets_apply_mirror(args) -> int:
    mgr = AssetManager(_install(args))
    rep = mgr.apply_mirror(Path(args.pack), dry_run=args.dry_run, on_progress=_console_progress)
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
    rep = AssetManager(_install(args)).restore_all()
    print(f"{rep['restored']} fichiers restaurés à l'original.")
    for f in rep["failed"]:
        print(f"  ÉCHEC {Path(f['target']).name} : {f['reason']}")
    if rep["failed"]:
        print(f"{len(rep['failed'])} fichier(s) non restauré(s) — le jeu reste modifié pour "
              "ceux-là. Corrige la cause puis relance `studio assets restore-all`.")
        return 1
    return 0


# --------------------------------------------------------------------------- packs
_PACK_LIB = packlib.DEFAULT_LIB


def _pack_kind(rep) -> str:
    cats = []
    if rep.cards:
        cats.append("cards")
    if rep.playmats:
        cats.append("playmats")
    if rep.cardbacks:
        cats.append("cardbacks")
    if rep.backgrounds:
        cats.append("backgrounds")
    if rep.translation:
        cats.append("translation")
    return cats[0] if len(cats) == 1 else "mixed"


def _find_pack(store, name: str) -> dict | None:
    return next((p for p in store.list("cosmetic_packs") if p["name"] == name), None)


def _resolve_cli_filter(args):
    """(only_categories, only_cards) depuis --only / --only-type / --leaders-only / --for-deck."""
    from .assets import cardmeta
    cats = set(args.only.split(",")) if getattr(args, "only", None) else None
    cards = None
    types = list(args.only_type.split(",")) if getattr(args, "only_type", None) else []
    if getattr(args, "leaders_only", False):
        types.append("leader")
    if types:
        cats = (cats or set()) | {"cards"}
        cards = set()
        for t in types:
            cards |= set(cardmeta.ids_of_type(t))
    for_decks = getattr(args, "for_deck", None)
    if for_decks:
        with LocalStore(Path(args.db)) as store:
            by = {d["name"]: d for d in store.list("decks")}
        ids = set()
        for nm in for_decks:
            d = by.get(nm)
            if d:
                ids |= set(d["cards"]) | {d["leader"]}
            else:
                print(f"  ⚠ deck inconnu ignoré : {nm}")
        cards = (cards or set()) | ids
    return cats, cards


def cmd_config_set_token(args) -> int:
    from .config import Config
    Config().set_github_token(args.token or None)
    state = "configuré" if Config().has_github_token() else "effacé"
    print(f"Token GitHub {state}. (jamais affiché ni loggé ; utilisé pour les dépôts privés)")
    return 0


def cmd_config_set_default_collection(args) -> int:
    """Source (chemin local ou URL) d'un collection.json (P10) à auto-analyser à l'ouverture
    de `studio ui` — permet de proposer ses propres packs GitHub sans les recoller à chaque
    fois (P12). Vide = effacer."""
    from .config import Config
    cfg = Config()
    cfg.set_default_collection_source(args.source or None)
    src = cfg.default_collection_source()
    print(f"Collection par défaut : {src}" if src else "Collection par défaut effacée.")
    return 0


def cmd_packs_add(args) -> int:
    from .config import Config
    inst = _install(args)
    cats, cards = _resolve_cli_filter(args)
    pack_dir, rep = packlib.add_pack(args.source, inst, name=args.name, lib_dir=_PACK_LIB,
                                     on_progress=_console_progress,
                                     only_categories=cats, only_cards=cards,
                                     token=Config().github_token())
    manifest = json.loads((pack_dir / "manifest.json").read_text())
    if args.follow:
        manifest["followed"] = True              # source re-téléchargeable via `packs update`
    with LocalStore(Path(args.db)) as store:
        existing = _find_pack(store, rep.name)
        rec = {"name": rep.name, "kind": _pack_kind(rep),
               "local_path": str(pack_dir), "manifest": manifest}
        if existing:
            rec["id"] = existing["id"]           # réécrase le même pack (nom unique)
        store.put("cosmetic_packs", rec)
    tag = " (suivi)" if args.follow else ""
    print(f"Pack « {rep.name} » ({_pack_kind(rep)}){tag} normalisé -> {pack_dir}")
    print(f"  {rep.summary()}")
    if rep.variants:
        print(f"  {len(rep.variants)} variante(s) (1re gardée) : "
              f"{[v['target'] for v in rep.variants[:5]]}")
    if rep.unclassified:
        print(f"  {len(rep.unclassified)} non classé(s) :")
        for u in rep.unclassified[:10]:
            print(f"    - {u['path']} : {u['reason']}")
        if len(rep.unclassified) > 10:
            print(f"    … et {len(rep.unclassified) - 10} de plus")
    if rep.filtered:
        print(f"  {len(rep.filtered)} fichier(s) hors périmètre (filtrés par choix, non importés)")
    print(f"Enregistré en bibliothèque. `studio packs apply {rep.name} --dry-run` "
          f"pour prévisualiser.")
    return 0


def cmd_packs_list(args) -> int:
    with LocalStore(Path(args.db)) as store:
        packs = store.list("cosmetic_packs")
    if not packs:
        print("Bibliothèque vide. `studio packs add <source>`.")
        return 0
    for p in packs:
        m = p.get("manifest") or {}
        print(f"{p['name']:<28} {p['kind']:<11} "
              f"cartes:{len(m.get('cards', [])):<4} "
              f"present:{m.get('present_in_install', 0):<4} {p['local_path']}")
    return 0


def cmd_packs_show(args) -> int:
    with LocalStore(Path(args.db)) as store:
        p = _find_pack(store, args.name)
    if p is None:
        raise SystemExit(f"Pack inconnu : {args.name} (voir `studio packs list`)")
    m = p.get("manifest") or {}
    print(f"Pack « {p['name']} » ({p['kind']}) — source {m.get('source', '?')}")
    print(f"  cartes={len(m.get('cards', []))} playmats={m.get('playmats')} "
          f"dos={m.get('cardbacks')} fonds={m.get('backgrounds')} "
          f"traduction={m.get('translation')}")
    print(f"  présents dans le jeu : {m.get('present_in_install', 0)}")
    if m.get("unclassified"):
        print(f"  non classés : {len(m['unclassified'])}")
    # état actif : swaps du manager dont la source est ce pack
    mgr = AssetManager(_install(args))
    mine = [s for s in mgr.status() if s.get("source") == f"pack:{p['name']}"]
    if mine:
        by_state: dict[str, int] = {}
        for s in mine:
            by_state[s["state"]] = by_state.get(s["state"], 0) + 1
        print(f"  appliqué : {by_state}")
    else:
        print("  appliqué : non")
    return 0


def cmd_packs_apply(args) -> int:
    with LocalStore(Path(args.db)) as store:
        p = _find_pack(store, args.name)
    if p is None:
        raise SystemExit(f"Pack inconnu : {args.name} (voir `studio packs list`)")
    pack_dir = Path(p["local_path"])
    if not pack_dir.exists():
        raise SystemExit(f"Dossier du pack introuvable : {pack_dir}")
    only = set(args.only.split(",")) if args.only else None
    mgr = AssetManager(_install(args))
    origin = f"pack:{p['name']}"
    rep = mgr.apply_mirror(pack_dir, origin=origin, dry_run=args.dry_run, only=only,
                           on_progress=_console_progress)
    verb = "seraient appliqués" if args.dry_run else "appliqués"
    print(f"{len(rep['applied'])} fichiers {verb}"
          + (f" (filtre : {args.only})" if only else "") + ".")
    # traduction : fusion (jamais écrasement) si présente et non filtrée
    txt = pack_dir / "TRANSLATION.txt"
    if txt.exists() and (only is None or "translation" in only):
        if args.dry_run:
            print("  traduction : serait fusionnée (clés officielles préservées).")
        else:
            mgr.apply_translation(txt, origin=origin)
            print("  traduction fusionnée.")
    if rep["collisions"]:
        print(f"  ⚠ {len(rep['collisions'])} collision(s) — ce pack prend le dessus "
              f"(le backup reste l'ORIGINAL) :")
        for c in rep["collisions"][:5]:
            print(f"    - {c['path']} (tenu par {c['previous']})")
    if rep["ignored"]:
        print(f"  {len(rep['ignored'])} ignoré(s) (cible absente / format).")
    if not args.dry_run and rep["applied"]:
        print("Ferme le sim avant/pendant. "
              f"`studio packs remove {p['name']}` restaure ce pack.")
    return 0


def _reapply_if_active(mgr, name: str, pack_dir: Path) -> int:
    """Ré-applique un pack s'il a des swaps actifs (après update). Renvoie le nb appliqué."""
    origin = f"pack:{name}"
    if any(s.get("source") == origin for s in mgr.status()):
        rep = mgr.apply_mirror(pack_dir, origin=origin, on_progress=_console_progress)
        txt = pack_dir / "TRANSLATION.txt"
        if txt.exists():
            mgr.apply_translation(txt, origin=origin)
        return len(rep["applied"])
    return 0


def cmd_packs_update(args) -> int:
    inst = _install(args)
    mgr = AssetManager(inst)
    with LocalStore(Path(args.db)) as store:
        packs = ([_find_pack(store, args.name)] if args.name
                 else [p for p in store.list("cosmetic_packs")
                       if (p.get("manifest") or {}).get("followed")])
        packs = [p for p in packs if p]
        if not packs:
            print("Aucun pack suivi à mettre à jour "
                  "(`studio packs add --follow <url>`).")
            return 0
        for p in packs:
            man = p.get("manifest") or {}
            src = man.get("source")
            if not man.get("followed") or not src:
                print(f"« {p['name']} » : non suivi, ignoré.")
                continue
            old_files = man.get("files", {})
            try:
                pack_dir, rep = packlib.add_pack(src, inst, name=p["name"], lib_dir=_PACK_LIB,
                                                 on_progress=_console_progress)
            except (packlib.PackError, OSError) as e:
                print(f"« {p['name']} » : échec du téléchargement/normalisation — {e}")
                continue
            new_man = json.loads((pack_dir / "manifest.json").read_text())
            new_man["followed"] = True
            new_files = new_man.get("files", {})
            changed = sorted(f for f in new_files if old_files.get(f) != new_files[f])
            removed = sorted(f for f in old_files if f not in new_files)
            store.put("cosmetic_packs", {**p, "manifest": new_man,
                                         "local_path": str(pack_dir),
                                         "kind": p["kind"]})
            if not changed and not removed:
                print(f"« {p['name']} » : déjà à jour.")
                continue
            print(f"« {p['name']} » : {len(changed)} modifié(s), {len(removed)} retiré(s).")
            n = _reapply_if_active(mgr, p["name"], pack_dir)
            if n:
                print(f"  ré-appliqué ({n} fichiers) — pack actif.")
    return 0


def cmd_packs_reapply(args) -> int:
    """Ré-applique les packs dont les swaps ont été écrasés par une mise à jour du sim."""
    inst = _install(args)
    mgr = AssetManager(inst)
    stale = {s["source"] for s in mgr.status()
             if s["state"] == "overwritten" and s.get("source", "").startswith("pack:")}
    if not stale:
        print("Aucun pack à ré-appliquer (rien n'a été écrasé).")
        return 0
    with LocalStore(Path(args.db)) as store:
        total = 0
        for origin in sorted(stale):
            name = origin[len("pack:"):]
            p = _find_pack(store, name)
            if p is None or not Path(p["local_path"]).exists():
                print(f"« {name} » : dossier de pack absent, impossible de ré-appliquer.")
                continue
            rep = mgr.apply_mirror(Path(p["local_path"]), origin=origin,
                                   on_progress=_console_progress)
            txt = Path(p["local_path"]) / "TRANSLATION.txt"
            if txt.exists():
                mgr.apply_translation(txt, origin=origin)
            total += len(rep["applied"])
            print(f"« {name} » : {len(rep['applied'])} fichiers ré-appliqués.")
    print(f"{total} fichier(s) ré-appliqué(s). Ferme le sim avant/pendant.")
    return 0


def cmd_packs_remove(args) -> int:
    with LocalStore(Path(args.db)) as store:
        p = _find_pack(store, args.name)
        if p is None:
            raise SystemExit(f"Pack inconnu : {args.name}")
        n = AssetManager(_install(args)).restore_source(f"pack:{p['name']}")
        store.delete("cosmetic_packs", p["id"])       # tombstone (synchronisable)
    print(f"{n} fichier(s) restauré(s) à l'original ; pack « {p['name']} » retiré "
          f"de la bibliothèque.")
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
    with LocalStore(Path(args.db)) as store:
        path = persist.persist_deck(store, deck, name,
                                    args.tags.split(",") if args.tags else [],
                                    inst.persistent)
    print(f"Deck « {name} » ({deck.leader}, {deck.total} cartes)")
    print(f"  -> sim  : {path}")
    print(f"  -> base : {args.db} (synchronisable)")
    return 0


def cmd_decks_list(args) -> int:
    with LocalStore(Path(args.db)) as store:
        for d in store.list("decks"):
            tags = ",".join(d["tags"]) or "-"
            print(f"{d['id']}  {d['name']:<28} {d['leader']:<12} tags:{tags:<20} "
                  f"({'dirty' if d['dirty'] else 'synced'})")
    return 0


def cmd_decks_sync_from_sim(args) -> int:
    """Importe les decks fabriqués DIRECTEMENT en jeu — purement additif, jamais de
    suppression (un deck disparu du dossier persistant est seulement signalé)."""
    inst = _install(args)
    with LocalStore(Path(args.db)) as store:
        r = importer.sync_with_store(inst.persistent, store)
    print(f"{len(r['new'])} nouveau(x), {len(r['updated'])} mis à jour, "
          f"{len(r['orphaned'])} introuvable(s) en jeu (gardé(s) en base)")
    for name in r["new"]:
        print(f"  + {name}")
    for name in r["updated"]:
        print(f"  ~ {name}")
    for name in r["orphaned"]:
        print(f"  ? {name} (plus dans le dossier du sim, gardé en base)")
    return 0


def cmd_decks_export_pack(args) -> int:
    """Empaquette des decks déjà en base en un deckpack.json (P6) — inverse de import-pack."""
    from .decks import deckpack
    ids = [s.strip() for s in args.ids.split(",") if s.strip()]
    with LocalStore(Path(args.db)) as store:
        resolved = []
        for deck_id in ids:
            row = store.get("decks", deck_id)
            if row is None or row.get("deleted"):
                raise SystemExit(f"deck introuvable : {deck_id} (voir `studio decks list`)")
            deck = importer.Decklist(leader=row["leader"], cards=row["cards"], name=row["name"])
            resolved.append(deckpack.ResolvedDeck(name=row["name"], tags=row.get("tags") or [],
                                                  deck=deck))
    pack = deckpack.generate(args.name, resolved, author=args.author)
    Path(args.out).write_text(json.dumps(pack, indent=2, ensure_ascii=False))
    print(f"« {args.name} » : {len(resolved)} deck(s) -> {args.out}")
    return 0


def cmd_decks_import_pack(args) -> int:
    """Importe une collection de decks (deckpack.json) d'un dossier/zip/URL."""
    from .decks import deckpack
    inst = _install(args)
    rep = deckpack.from_source(args.source, packlib.ingest, _PACK_LIB / ".deckwork")
    with LocalStore(Path(args.db)) as store:
        # Un échec d'ÉCRITURE ne doit pas faire perdre les decks suivants (même politique que
        # la résolution, et que la route API équivalente).
        persisted, failed = [], list(rep.failed)
        for rd in rep.imported:
            try:
                persist.persist_deck(store, rd.deck, rd.name, rd.tags, inst.persistent)
                persisted.append(rd)
            except OSError as e:
                failed.append({"name": rd.name, "reason": str(e)})
    # Résumé recalculé sur ce qui a RÉELLEMENT été persisté : `rep.summary()` ne connaît que
    # les échecs de résolution, pas ceux d'écriture.
    msg = f"« {rep.name} » : {len(persisted)} deck(s) importé(s)"
    if failed:
        msg += f", {len(failed)} en échec"
    if rep.warnings:
        msg += f", {len(rep.warnings)} avertissement(s)"
    print(msg)
    for d in persisted:
        print(f"  ✓ {d.name} ({d.deck.leader}, {d.deck.total} cartes)"
              + (f" [{', '.join(d.tags)}]" if d.tags else ""))
    for f in failed:
        print(f"  ✗ {f['name']} : {f['reason']}")
    for w in rep.warnings:
        print(f"  ⚠ {w}")
    # Code de sortie honnête : un script qui enchaîne `import-pack` doit pouvoir détecter
    # qu'une partie du pack n'est pas passée. Renvoyer 0 avec des ✗ à l'écran rend l'échec
    # invisible en CI ou dans un enchaînement `&&`.
    return 1 if failed else 0


def cmd_decks_validate_pack(args) -> int:
    """Contrôle à blanc d'un deckpack (dry-run) : résout tout, n'écrit rien."""
    from .decks import deckpack
    rep = deckpack.from_source(args.source, packlib.ingest, _PACK_LIB / ".deckwork")
    print(rep.summary() + "  (contrôle à blanc — rien écrit)")
    for d in rep.imported:
        print(f"  ✓ {d.name} ({d.deck.leader}, {d.deck.total} cartes)"
              + (f" [{', '.join(d.tags)}]" if d.tags else ""))
    for f in rep.failed:
        print(f"  ✗ {f['name']} : {f['reason']}")
    for w in rep.warnings:
        print(f"  ⚠ {w}")
    return 1 if rep.failed else 0


# --------------------------------------------------------------------------- repos
def _print_repo_line(fam: str, s, repobuild) -> None:
    line = f"  • {fam}/ — {s.files} fichiers, {s.bytes // (1024*1024)} Mo"
    if s.by_type:
        line += " [" + ", ".join(f"{k}:{v}" for k, v in sorted(s.by_type.items())) + "]"
    bits = []
    if s.added:
        bits.append(f"+{len(s.added)} ajoutés")
    if s.changed:
        bits.append(f"~{len(s.changed)} modifiés")
    if s.orphans:
        bits.append(f"{len(s.orphans)} orphelin(s) (plus dans la source, non supprimés)")
    if bits:
        line += " — " + ", ".join(bits)
    print(line)
    if s.oversize:
        print(f"    ⚠ dépôt > {repobuild.GITHUB_SOFT_LIMIT // (1024*1024)} Mo — "
              "envisage de scinder (déplace un sous-dossier de type dans un autre dépôt).")


def _print_repo_report(rep, repobuild) -> None:
    print(rep.summary())
    for fam, s in sorted(rep.repos.items()):
        _print_repo_line(fam, s, repobuild)
    if rep.collisions:
        print(f"  ⚠ {len(rep.collisions)} collision(s) (dernière source gardée).")
    if rep.unclassified:
        print(f"  {len(rep.unclassified)} fichier(s) non classé(s) (ignorés).")


def cmd_repos_build(args) -> int:
    """Construit des dépôts d'images par famille depuis des sources (GitHub/Dropbox/Drive)."""
    from .assets import repobuild
    from .config import Config
    rep = repobuild.build(
        args.source, Path(args.out), cards_as=args.cards_as,
        split_cards_by_type=not args.no_split, git_init=not args.no_git,
        path_prefix=args.path_prefix, lang=args.lang, token=Config().github_token(),
        collection_label=args.collection_label, collection_group=args.collection_group,
        on_progress=_console_progress)
    _print_repo_report(rep, repobuild)
    print(f"\nDépôts prêts dans {args.out}. Prochaine étape (à faire par toi) :")
    print("  cd <dépôt> && git add -A && git commit -m init")
    print("  git remote add origin git@github.com:<toi>/<dépôt-privé>.git && git push -u origin main")
    print(f"\nÀ chaque sortie de set : studio repos update --out {args.out}")
    print(f"Après le push, complète les URLs dans {args.out}/collection.json "
         "(l'UI web sait déjà importer ce manifeste groupé — section « Importer une "
         "collection », cf. chantier P10 du plan). Rappel : deux familles de CARTES qui "
         "écrasent les MÊMES fichiers cibles (ex. alt-arts anglais ET traduction FR) doivent "
         "partager le même --collection-group, même avec des --cards-as différents — sinon "
         "l'UI les propose comme des compléments indépendants qui peuvent se marcher dessus.")
    if (args.collection_label or args.collection_group) and not Config().default_collection_source():
        print(f"\nAstuce (P12) : une fois les URLs complétées, "
             f"`studio config set-default-collection {args.out}/collection.json` propose "
             "ces packs automatiquement à l'ouverture de `studio ui` (zéro copier-coller).")
    return 0


def cmd_repos_update(args) -> int:
    """Rejoue les builds déjà lancés sous --out (mêmes sources, sans les retaper) et affiche
    le diff (ajoutés/modifiés/orphelins) par dépôt, pour savoir quoi committer."""
    from .assets import repobuild
    from .config import Config
    reports = repobuild.update(Path(args.out), token=Config().github_token(),
                               on_progress=_console_progress)
    for rep in reports:
        _print_repo_report(rep, repobuild)
    print("\nPour chaque dépôt avec des changements :")
    print("  cd <dépôt> && git add -A && git commit -m 'maj <nom du set>' && git push")
    return 0


# --------------------------------------------------------------------------- sync
def cmd_ui(args) -> int:
    from .api.server import run_ui
    return run_ui(install=_install(args), db_path=args.db, port=args.port,
                  open_browser=not args.no_open)


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

    pp = sub.add_parser("packs", help="bibliothèque de packs d'assets (normaliser + appliquer)")
    sp = pp.add_subparsers(dest="sub", required=True)
    pad = sp.add_parser("add", help="normaliser une source (dossier/zip/URL) en pack")
    pad.add_argument("source", help="dossier, .zip, ou URL (GitHub / Dropbox partagé)")
    pad.add_argument("--name", default=None, help="nom du pack en bibliothèque")
    pad.add_argument("--follow", action="store_true",
                     help="source suivie : re-téléchargeable via `packs update`")
    pad.add_argument("--only", default=None, metavar="cards,playmats,don,...",
                     help="import sélectif : catégories à garder (économie disque)")
    pad.add_argument("--only-type", default=None, metavar="leader,event,stage,character",
                     help="import sélectif : uniquement ces types de cartes")
    pad.add_argument("--leaders-only", action="store_true",
                     help="raccourci de --only-type leader")
    pad.add_argument("--for-deck", action="append", metavar="NOM",
                     help="import sélectif : uniquement les cartes de ce deck (répétable). "
                          "Sur une source GitHub, ne télécharge QUE le nécessaire.")
    pad.set_defaults(func=cmd_packs_add)
    pup = sp.add_parser("update", help="re-télécharger les packs suivis (delta + ré-apply)")
    pup.add_argument("name", nargs="?", default=None, help="un pack précis (sinon tous)")
    pup.set_defaults(func=cmd_packs_update)
    sp.add_parser("reapply",
                  help="ré-appliquer les packs écrasés par une màj du sim"
                  ).set_defaults(func=cmd_packs_reapply)
    sp.add_parser("list", help="lister la bibliothèque").set_defaults(func=cmd_packs_list)
    psh = sp.add_parser("show", help="détail d'un pack + état appliqué")
    psh.add_argument("name")
    psh.set_defaults(func=cmd_packs_show)
    pap = sp.add_parser("apply", help="appliquer un pack au jeu")
    pap.add_argument("name")
    pap.add_argument("--only", default=None,
                     help="catégories (cards,playmats,cardbacks,backgrounds,translation)")
    pap.add_argument("--dry-run", action="store_true", help="prévisualiser sans écrire")
    pap.set_defaults(func=cmd_packs_apply)
    prm = sp.add_parser("remove", help="restaurer les originaux et retirer le pack")
    prm.add_argument("name")
    prm.set_defaults(func=cmd_packs_remove)

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

    dsy = sd.add_parser("sync-from-sim",
                        help="importer les decks créés directement en jeu (additif, jamais "
                             "de suppression)")
    dsy.set_defaults(func=cmd_decks_sync_from_sim)

    dep = sd.add_parser("export-pack",
                        help="empaqueter des decks déjà en base en deckpack.json (partage)")
    dep.add_argument("--ids", required=True,
                     help="ids de deck séparés par des virgules (voir `decks list`)")
    dep.add_argument("--name", required=True)
    dep.add_argument("--author", default=None)
    dep.add_argument("--out", required=True, help="chemin de sortie du deckpack.json")
    dep.set_defaults(func=cmd_decks_export_pack)

    dip = sd.add_parser("import-pack",
                        help="importer une collection de decks (deckpack.json : dossier/zip/URL)")
    dip.add_argument("source", help="dossier, .zip ou URL contenant un deckpack.json")
    dip.set_defaults(func=cmd_decks_import_pack)

    dvp = sd.add_parser("validate-pack",
                        help="contrôler un deckpack sans l'importer (dry-run)")
    dvp.add_argument("source", help="dossier, .zip ou URL contenant un deckpack.json")
    dvp.set_defaults(func=cmd_decks_validate_pack)

    pdoc = sub.add_parser("doctor",
                          help="diagnostic de l'installation (à joindre à un rapport de bug)")
    pdoc.add_argument("--state-dir", default=None,
                      help="dossier d'état à inspecter (défaut : ~/.optcgsim-studio)")
    pdoc.set_defaults(func=cmd_doctor)

    pui = sub.add_parser("ui", help="lance l'interface web locale (zéro dépendance)")
    pui.add_argument("--port", type=int, default=8770)
    pui.add_argument("--no-open", action="store_true", help="ne pas ouvrir le navigateur")
    pui.set_defaults(func=cmd_ui)

    ps = sub.add_parser("sync", help="synchronisation cloud (mode connecté)")
    ps.add_argument("--url", default=None)
    ps.add_argument("--token", default=None)
    ps.set_defaults(func=cmd_sync)

    pr = sub.add_parser("repos", help="construire des dépôts d'images par famille (à pousser)")
    sr = pr.add_subparsers(dest="sub", required=True)
    rb = sr.add_parser("build",
                       help="générer les dépôts par famille depuis des sources "
                            "(GitHub/Dropbox/Drive fichier-zip)")
    rb.add_argument("source", nargs="+",
                    help="une ou plusieurs sources (URL GitHub/Dropbox/Drive, zip ou dossier)")
    rb.add_argument("--out", required=True, help="dossier racine où écrire les dépôts")
    rb.add_argument("--cards-as", default="alt",
                    help="famille des CARTES de ces sources : alt (défaut) | translated | <nom>")
    rb.add_argument("--no-split", action="store_true",
                    help="ne pas sous-classer les cartes par type (Leaders/Events/…)")
    rb.add_argument("--no-git", action="store_true",
                    help="ne pas exécuter git init dans chaque dépôt")
    rb.add_argument("--path-prefix", default=None,
                    help="ne traiter que les CARTES/DON!! sous ce sous-dossier de la source "
                         "(ex. FR_classique) — utile quand une même source mélange plusieurs "
                         "variantes de la même carte (classique/alternative) : un build par "
                         "variante avec un --cards-as et un --path-prefix distincts évite la "
                         "collision. N'exclut jamais traduction/playmats/dos de carte, qui "
                         "sont des assets partagés hors sujet du préfixe.")
    rb.add_argument("--lang", default=None,
                    help="langue du TRANSLATION.txt de ces sources (ex. fr, es) -> dépôt "
                         "translations-<lang>, INDÉPENDANT de --cards-as. Sans ça, deux "
                         "variantes d'art (classique/full-art) ou deux langues partageant le "
                         "même alias écraseraient le même TRANSLATION.txt ; --lang fr regroupe "
                         "toutes les variantes d'une langue dans un seul dépôt de traduction.")
    rb.add_argument("--collection-label", default=None,
                    help="[P10] libellé affiché pour cette famille dans <out>/collection.json "
                         "(défaut : le nom de la famille), consommé par l'UI web (section "
                         "« Importer une collection »). Persisté : pas besoin de le retaper à "
                         "chaque `repos update`.")
    rb.add_argument("--collection-group", default=None,
                    help="[P10] marque cette famille comme une VARIANTE ALTERNATIVE de ce "
                         "groupe nommé (ex. --collection-group cards pour classique ET "
                         "full-art) — l'UI la propose en radio, un seul pack du groupe importé "
                         "à la fois, jamais tous. IMPORTANT : à utiliser pour TOUTE famille de "
                         "CARTES qui écrase les mêmes fichiers cibles qu'une autre, même avec "
                         "un --cards-as différent (ex. cards-alt anglais vs translated-fr-* — "
                         "tous les trois réécrivent Cards/<SET>/<ID>.png, donc tous les trois "
                         "doivent partager --collection-group cards). Omis = famille "
                         "complémentaire (case cochée par défaut, toujours importée).")
    rb.set_defaults(func=cmd_repos_build)

    ru = sr.add_parser("update",
                       help="rejouer les builds déjà lancés sous --out (mêmes sources, "
                            "sans les retaper) et afficher le diff — à faire à chaque sortie de set")
    ru.add_argument("--out", required=True,
                    help="dossier déjà construit par `repos build` (contient .repos-build.json)")
    ru.set_defaults(func=cmd_repos_update)

    pc = sub.add_parser("config", help="configuration locale (token GitHub, collection par défaut…)")
    sc = pc.add_subparsers(dest="sub", required=True)
    sct = sc.add_parser("set-github-token",
                        help="token pour les dépôts privés (import sélectif) ; vide = effacer")
    sct.add_argument("token", nargs="?", default=None)
    sct.set_defaults(func=cmd_config_set_token)

    scdc = sc.add_parser("set-default-collection",
                         help="collection.json (chemin local ou URL) auto-analysée à "
                              "l'ouverture de `studio ui` (P12) ; vide = effacer")
    scdc.add_argument("source", nargs="?", default=None)
    scdc.set_defaults(func=cmd_config_set_default_collection)
    return p


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.func(args)
    except (AssetError, importer.ImportError_, packlib.PackError) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        # `decks import --url`, `sync`, `packs add` sortaient une trace brute sur une simple
        # coupure réseau. On donne aussi l'indice cert macOS, comme le fait déjà l'UI.
        msg = f"Erreur réseau : {e.reason if hasattr(e, 'reason') else e}"
        if is_cert_error(e):
            msg += f"\n{CERT_FIX_HINT}"
        print(msg, file=sys.stderr)
        return 1
    except OSError as e:
        # disque plein, dossier du jeu non écrivable, chemin inexistant…
        print(f"Erreur système : {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
