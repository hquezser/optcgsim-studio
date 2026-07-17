"""Moteur de synchronisation générique : LocalStore <-> backend distant.

Algorithme (par entité, offline-first, mono-utilisateur multi-appareils) :
  1. PUSH : chaque enregistrement local `dirty` part vers le remote. Le remote applique
     lui aussi last-write-wins et renvoie sa révision -> `mark_clean`.
  2. PULL : `remote.changed_since(cursor)` ; chaque enregistrement entrant est appliqué
     en local SEULEMENT si son updated_at est strictement plus récent (LWW) — un
     enregistrement local dirty plus récent gagne et repartira au prochain push.
  3. Avance du curseur au max(updated_at) reçu.

Les tombstones circulent comme des enregistrements normaux (deleted=1) : une suppression
sur l'appareil A supprime sur B, sans résurrection possible par un vieux record (LWW).

Le « remote » n'est qu'un SyncStore : l'implémentation de référence pour les tests est un
FakeRemote en mémoire ; la production branchera RemoteStore (REST/Supabase, voir remote.py)
sans changer une ligne d'ici.
"""

from __future__ import annotations

from .base import ENTITIES, SyncStore
from .local import LocalStore


def synchronize(local: LocalStore, remote: SyncStore,
                entities: tuple[str, ...] = ENTITIES) -> dict[str, dict]:
    report: dict[str, dict] = {}
    for entity in entities:
        pushed = pulled = skipped = 0

        # 1. PUSH des modifications locales
        for rec in local.dirty_records(entity):
            remote_cur = remote.get(entity, rec["id"])
            if remote_cur is None or remote_cur["updated_at"] <= rec["updated_at"]:
                stored = remote.put(entity, rec, from_sync=True)
                local.mark_clean(entity, rec["id"], stored.get("remote_rev"))
                pushed += 1
            # sinon : le remote est plus récent -> le pull ci-dessous tranchera

        # 2. PULL des modifications distantes
        cursor = local.sync_cursor(entity)
        newest = cursor
        for rec in remote.changed_since(entity, cursor):
            newest = max(newest, rec["updated_at"])
            mine = local.get(entity, rec["id"])
            if mine is not None and mine["updated_at"] >= rec["updated_at"]:
                skipped += 1               # ma version est plus récente : LWW local
                continue
            local.put(entity, rec, from_sync=True)
            pulled += 1

        # 3. curseur
        if newest > cursor:
            local.set_sync_cursor(entity, newest)
        report[entity] = {"pushed": pushed, "pulled": pulled, "skipped": skipped}
    return report
