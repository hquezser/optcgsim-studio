"""Protocole de stockage synchronisable — l'abstraction qui découple local et cloud.

Toute la logique métier (frontend compris) ne parle QU'À ce protocole : brancher le mode
connecté = fournir une seconde implémentation, zéro changement en amont. Les entités sont
des dicts JSON-compatibles portant les colonnes de réplication (voir db/schema.sql).

Contrat de réplication :
  - `put` estampille updated_at (epoch UTC), device_id et dirty=1 ;
  - `delete` pose un TOMBSTONE (deleted=1, dirty=1) — jamais de suppression physique
    tant que la propagation n'est pas confirmée ;
  - la résolution de conflit est LAST-WRITE-WINS par updated_at (suffisant pour des
    données mono-utilisateur multi-appareils ; documenter tout futur besoin collaboratif).
"""

from __future__ import annotations

from typing import Iterable, Protocol

ENTITIES = ("profiles", "decks", "cosmetic_packs")


class SyncStore(Protocol):
    device_id: str

    def list(self, entity: str, include_deleted: bool = False) -> list[dict]: ...

    def get(self, entity: str, record_id: str) -> dict | None: ...

    def put(self, entity: str, record: dict, *, from_sync: bool = False) -> dict:
        """Écrit un enregistrement. `from_sync=True` = réplication entrante : on préserve
        updated_at/device d'origine et dirty=0 (sinon boucle de sync infinie)."""
        ...

    def delete(self, entity: str, record_id: str) -> None: ...

    def changed_since(self, entity: str, ts: float) -> Iterable[dict]:
        """Enregistrements (tombstones inclus) modifiés strictement après `ts`."""
        ...
