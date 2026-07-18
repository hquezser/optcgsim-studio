"""API JSON locale du studio + service de l'UI web auto-suffisante.

Serveur de la BIBLIOTHÈQUE STANDARD uniquement (http.server) : aucune dépendance à installer,
`studio ui` suffit. Le contrat JSON est le MÊME que consommera un futur client Next.js/Tauri
(mobile) — la logique reste côté Python (« core mince côté rendu »).

Endpoints (préfixe /api) :
    GET  /inventory                 -> ce que l'install expose
    GET  /packs                     -> bibliothèque (+ état appliqué)
    POST /packs/add {source}        -> normalise une source (dossier/zip/URL)
    POST /packs/upload  (corps=zip, ?name=) -> normalise un zip uploadé (drag&drop)
    POST /packs/<name>/apply {only?, dry_run?}
    POST /packs/<name>/remove
    POST /packs/update {name?}      /  POST /packs/reapply
    GET  /packs/<name>/coverage     -> couverture par deck (le crochet d'adoption)
    GET  /decks                     -> decks en base
    POST /decks/import {text?|url?, name?, tags?}

Écriture (apply/remove) : passe par AssetManager -> mêmes garde-fous (backup, atomique,
restore). Le serveur écoute sur 127.0.0.1 uniquement (jamais exposé au réseau).
"""

from __future__ import annotations

import json
import re
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..assets import packlib
from ..assets.manager import AssetError, AssetManager
from ..decks import importer
from ..gamepaths import GameInstall, locate
from ..storage.local import DEFAULT_DB, LocalStore

STATIC = Path(__file__).parent / "static"


def _pack_kind(rep) -> str:
    cats = [k for k, v in (("cards", rep.cards), ("playmats", rep.playmats),
                           ("cardbacks", rep.cardbacks), ("backgrounds", rep.backgrounds),
                           ("translation", rep.translation)) if v]
    return cats[0] if len(cats) == 1 else "mixed"


class StudioService:
    """Logique métier de l'API — indépendante du transport HTTP (testable directement)."""

    def __init__(self, install: GameInstall, db_path: str = str(DEFAULT_DB),
                 lib_dir: Path = packlib.DEFAULT_LIB):
        self.install = install
        self.db_path = db_path
        self.lib_dir = Path(lib_dir)
        self.mgr = AssetManager(install)

    def _store(self) -> LocalStore:
        return LocalStore(Path(self.db_path))

    def _find(self, store, name):
        return next((p for p in store.list("cosmetic_packs") if p["name"] == name), None)

    # ------------------------------------------------------------ lecture
    def inventory(self) -> dict:
        return self.mgr.inventory()

    def packs(self) -> list[dict]:
        status = self.mgr.status()
        applied: dict[str, int] = {}
        for s in status:
            src = s.get("source", "")
            if src.startswith("pack:"):
                applied[src[5:]] = applied.get(src[5:], 0) + 1
        with self._store() as store:
            out = []
            for p in store.list("cosmetic_packs"):
                m = p.get("manifest") or {}
                out.append({
                    "name": p["name"], "kind": p["kind"],
                    "cards": len(m.get("cards", [])),
                    "playmats": m.get("playmats", []),
                    "translation": m.get("translation", False),
                    "present_in_install": m.get("present_in_install", 0),
                    "followed": m.get("followed", False),
                    "unclassified": len(m.get("unclassified", [])),
                    "applied": applied.get(p["name"], 0),
                })
        return out

    def decks(self) -> list[dict]:
        with self._store() as store:
            return [{"name": d["name"], "leader": d["leader"],
                     "cards": d["cards"], "tags": d["tags"]}
                    for d in store.list("decks")]

    def coverage(self, name: str) -> dict:
        """Pour chaque deck : combien de ses cartes ce pack re-skine. Le crochet d'adoption :
        « ce pack couvre 38/51 cartes de ton deck »."""
        with self._store() as store:
            pack = self._find(store, name)
            if pack is None:
                raise KeyError(name)
            pack_cards = set((pack.get("manifest") or {}).get("cards", []))
            decks = store.list("decks")
        rows = []
        for d in decks:
            deck_cards = set(d["cards"]) | {d["leader"]}
            hit = sorted(deck_cards & pack_cards)
            rows.append({
                "deck": d["name"], "leader": d["leader"],
                "covered": len(hit), "total": len(deck_cards),
                "missing": sorted(deck_cards - pack_cards),
                "pct": round(100 * len(hit) / len(deck_cards), 1) if deck_cards else 0.0,
            })
        rows.sort(key=lambda r: r["pct"], reverse=True)
        return {"pack": name, "cards_in_pack": len(pack_cards), "decks": rows}

    # ------------------------------------------------------------ écriture
    def add_source(self, source: str, name: str | None = None,
                   follow: bool = False) -> dict:
        pack_dir, rep = packlib.add_pack(source, self.install, name=name,
                                         lib_dir=self.lib_dir)
        return self._register(pack_dir, rep, follow)

    def add_zip_bytes(self, data: bytes, name: str) -> dict:
        tmp = Path(tempfile.mkdtemp(prefix="studio-up-"))
        zpath = tmp / (re.sub(r"[^\w.-]", "_", name) or "upload.zip")
        zpath.write_bytes(data)
        base = re.sub(r"\.zip$", "", name, flags=re.I)
        return self.add_source(str(zpath), name=re.sub(r"[^\w.-]", "_", base) or "pack")

    def _register(self, pack_dir: Path, rep, follow: bool) -> dict:
        manifest = json.loads((pack_dir / "manifest.json").read_text())
        if follow:
            manifest["followed"] = True
        with self._store() as store:
            existing = self._find(store, rep.name)
            rec = {"name": rep.name, "kind": _pack_kind(rep),
                   "local_path": str(pack_dir), "manifest": manifest}
            if existing:
                rec["id"] = existing["id"]
            store.put("cosmetic_packs", rec)
        return {"name": rep.name, "kind": _pack_kind(rep), "summary": rep.summary(),
                "cards": rep.cards, "unclassified": rep.unclassified,
                "present_in_install": rep.present_in_install}

    def apply(self, name: str, only: set[str] | None = None,
              dry_run: bool = False) -> dict:
        with self._store() as store:
            pack = self._find(store, name)
        if pack is None:
            raise KeyError(name)
        pack_dir = Path(pack["local_path"])
        origin = f"pack:{name}"
        rep = self.mgr.apply_mirror(pack_dir, origin=origin, dry_run=dry_run, only=only)
        txt = pack_dir / "TRANSLATION.txt"
        translated = False
        if txt.exists() and (only is None or "translation" in only) and not dry_run:
            self.mgr.apply_translation(txt, origin=origin)
            translated = True
        return {"applied": rep["applied"], "collisions": rep["collisions"],
                "ignored": len(rep["ignored"]), "translated": translated,
                "dry_run": dry_run}

    def remove(self, name: str) -> dict:
        with self._store() as store:
            pack = self._find(store, name)
            if pack is None:
                raise KeyError(name)
            n = self.mgr.restore_source(f"pack:{name}")
            store.delete("cosmetic_packs", pack["id"])
        return {"restored": n, "name": name}

    def import_deck(self, text: str | None = None, url: str | None = None,
                    name: str | None = None, tags: list[str] | None = None) -> dict:
        if url:
            deck = importer.from_url(url, name=name)
        elif text:
            deck = importer.parse_text(text, name=name, source="ui")
        else:
            raise AssetError("Fournir `text` ou `url`")
        deck_name = name or f"Import {deck.leader}"
        path = deck.save_to_sim(deck_name, self.install.persistent)
        with self._store() as store:
            profiles = store.list("profiles")
            prof = profiles[0] if profiles else store.put(
                "profiles", {"name": "default", "prefs": {}})
            store.put("decks", {"profile_id": prof["id"], "name": deck_name,
                                "leader": deck.leader, "cards": deck.cards,
                                "tags": tags or [], "source": deck.source})
        return {"name": deck_name, "leader": deck.leader, "total": deck.total,
                "path": str(path)}


# --------------------------------------------------------------------------- HTTP
def make_handler(svc: StudioService):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silencieux
            pass

        def _send(self, code: int, payload, ctype="application/json"):
            body = (json.dumps(payload).encode() if ctype == "application/json"
                    else payload)
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body_json(self) -> dict:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b""
            return json.loads(raw or b"{}")

        # -------- routing
        def do_GET(self):
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, (STATIC / "index.html").read_bytes(),
                                      "text/html; charset=utf-8")
                if path == "/api/inventory":
                    return self._send(200, svc.inventory())
                if path == "/api/packs":
                    return self._send(200, svc.packs())
                if path == "/api/decks":
                    return self._send(200, svc.decks())
                m = re.match(r"^/api/packs/([^/]+)/coverage$", path)
                if m:
                    return self._send(200, svc.coverage(_dec(m.group(1))))
                return self._send(404, {"error": "not found"})
            except KeyError as e:
                return self._send(404, {"error": f"introuvable : {e}"})
            except Exception as e:  # noqa: BLE001 — surface l'erreur au client local
                return self._send(400, {"error": str(e)})

        def do_POST(self):
            u = urlparse(self.path)
            path, qs = u.path, parse_qs(u.query)
            try:
                if path == "/api/packs/add":
                    b = self._body_json()
                    return self._send(200, svc.add_source(
                        b["source"], b.get("name"), b.get("follow", False)))
                if path == "/api/packs/upload":
                    n = int(self.headers.get("Content-Length", 0))
                    data = self.rfile.read(n)
                    fname = self.headers.get("X-Filename", "upload.zip")
                    return self._send(200, svc.add_zip_bytes(data, fname))
                if path == "/api/packs/update":
                    return self._send(200, {"ok": True, "note": "voir CLI `packs update`"})
                m = re.match(r"^/api/packs/([^/]+)/apply$", path)
                if m:
                    b = self._body_json()
                    only = set(b["only"]) if b.get("only") else None
                    return self._send(200, svc.apply(
                        _dec(m.group(1)), only=only, dry_run=b.get("dry_run", False)))
                m = re.match(r"^/api/packs/([^/]+)/remove$", path)
                if m:
                    return self._send(200, svc.remove(_dec(m.group(1))))
                if path == "/api/decks/import":
                    b = self._body_json()
                    return self._send(200, svc.import_deck(
                        text=b.get("text"), url=b.get("url"),
                        name=b.get("name"), tags=b.get("tags")))
                return self._send(404, {"error": "not found"})
            except KeyError as e:
                return self._send(404, {"error": f"introuvable : {e}"})
            except (AssetError, importer.ImportError_, packlib.PackError) as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {"error": str(e)})

    return Handler


def _dec(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def run_ui(install: GameInstall | None = None, db_path: str = str(DEFAULT_DB),
           port: int = 8770, open_browser: bool = True) -> int:
    install = install or locate()
    if install is None:
        print("Installation OPTCGSim introuvable — préciser --app-root.")
        return 1
    svc = StudioService(install, db_path)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(svc))
    url = f"http://127.0.0.1:{port}/"
    print(f"OPTCGSim Studio — UI locale : {url}")
    print("Ctrl-C pour arrêter.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")
    finally:
        httpd.server_close()
    return 0
