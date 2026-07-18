"""Backend distant — implémentations du protocole SyncStore côté cloud.

Deux variantes prévues, MÊME interface (le moteur sync.py ne voit pas la différence) :

1. `RestRemote` : API REST minimaliste à héberger soi-même. Contrat attendu :
       GET  /v1/{entity}?since={epoch}   -> [records]           (tombstones inclus)
       GET  /v1/{entity}/{id}            -> record | 404
       PUT  /v1/{entity}/{id}            -> record (avec remote_rev serveur)
   Auth : Bearer token. Le serveur applique LWW par updated_at et n'écrase jamais un
   record plus récent (mêmes règles que le client -> convergence).

2. Supabase/PostgreSQL : trois tables miroir du schéma local (profiles/decks/
   cosmetic_packs, mêmes colonnes + user_id) protégées par Row-Level-Security
   (user_id = auth.uid()), consommées via PostgREST — c'est exactement le contrat REST
   ci-dessus, fourni par Supabase sans serveur à écrire.

`FakeRemote` (en mémoire) est l'implémentation de RÉFÉRENCE : elle fixe la sémantique que
tout backend doit respecter, et sert aux tests de convergence multi-appareils.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Iterable

from ..nettls import CERT_FIX_HINT, is_cert_error, ssl_context


class FakeRemote:
    """Backend en mémoire — référence de sémantique + tests. device_id 'remote'."""

    device_id = "remote"

    def __init__(self):
        self._data: dict[str, dict[str, dict]] = {}
        self._rev = 0

    def list(self, entity: str, include_deleted: bool = False) -> list[dict]:
        rows = list(self._data.get(entity, {}).values())
        return rows if include_deleted else [r for r in rows if not r.get("deleted")]

    def get(self, entity: str, record_id: str) -> dict | None:
        return self._data.get(entity, {}).get(record_id)

    def put(self, entity: str, record: dict, *, from_sync: bool = False) -> dict:
        cur = self.get(entity, record["id"])
        if cur is not None and cur["updated_at"] > record["updated_at"]:
            return cur                      # LWW serveur : le plus récent reste
        self._rev += 1
        stored = {**record, "dirty": 0, "remote_rev": f"r{self._rev}"}
        self._data.setdefault(entity, {})[record["id"]] = stored
        return stored

    def delete(self, entity: str, record_id: str) -> None:
        cur = self.get(entity, record_id)
        if cur:
            self.put(entity, {**cur, "deleted": 1, "updated_at": time.time()},
                     from_sync=True)

    def changed_since(self, entity: str, ts: float) -> Iterable[dict]:
        return [r for r in self._data.get(entity, {}).values()
                if r["updated_at"] > ts]


class RestRemote:
    """Client REST du contrat documenté ci-dessus. Réseau réel -> non couvert par les
    tests unitaires (FakeRemote fixe la sémantique) ; à valider contre un serveur réel."""

    device_id = "remote"

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None):
        req = urllib.request.Request(
            f"{self.base}{path}", method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/json"},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(
                    req, timeout=self.timeout, context=ssl_context()) as resp:
                return json.loads(resp.read().decode() or "null")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        except urllib.error.URLError as e:
            # voir packlib._download : urlopen() enveloppe le handshake SSL dans un URLError.
            if is_cert_error(e):
                raise RuntimeError(CERT_FIX_HINT) from e
            raise

    def list(self, entity: str, include_deleted: bool = False) -> list[dict]:
        rows = self._req("GET", f"/v1/{entity}?since=0") or []
        return rows if include_deleted else [r for r in rows if not r.get("deleted")]

    def get(self, entity: str, record_id: str) -> dict | None:
        return self._req("GET", f"/v1/{entity}/{record_id}")

    def put(self, entity: str, record: dict, *, from_sync: bool = False) -> dict:
        return self._req("PUT", f"/v1/{entity}/{record['id']}", record)

    def delete(self, entity: str, record_id: str) -> None:
        cur = self.get(entity, record_id)
        if cur:
            self.put(entity, {**cur, "deleted": 1, "updated_at": time.time()})

    def changed_since(self, entity: str, ts: float) -> Iterable[dict]:
        return self._req("GET", f"/v1/{entity}?since={ts}") or []
