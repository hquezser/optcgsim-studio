"""Implémentation locale (SQLite) du protocole SyncStore — le mode déconnecté par défaut."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Iterable

from .base import ENTITIES

_SCHEMA = Path(__file__).parent.parent / "db" / "schema.sql"
DEFAULT_DB = Path.home() / ".optcgsim-studio" / "studio.db"

# Colonnes JSON par entité (sérialisées à l'écriture, désérialisées à la lecture).
_JSON_COLS = {"profiles": ("prefs",), "decks": ("cards", "tags"),
              "cosmetic_packs": ("manifest",)}


def _device_id(state_dir: Path) -> str:
    """Identifiant stable de CET appareil (fichier local, créé une fois)."""
    f = state_dir / "device_id"
    if f.exists():
        return f.read_text().strip()
    did = uuid.uuid4().hex[:12]
    state_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(did)
    return did


class LocalStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA.read_text())
        self.device_id = _device_id(db_path.parent)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.conn.commit()
        self.conn.close()

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _check_entity(entity: str) -> None:
        if entity not in ENTITIES:
            raise ValueError(f"Entité inconnue : {entity}")

    def _decode(self, entity: str, row: sqlite3.Row) -> dict:
        d = dict(row)
        for col in _JSON_COLS[entity]:
            if isinstance(d.get(col), str):
                d[col] = json.loads(d[col])
        return d

    def _encode(self, entity: str, record: dict) -> dict:
        d = dict(record)
        for col in _JSON_COLS[entity]:
            if col in d and not isinstance(d[col], str):
                d[col] = json.dumps(d[col], ensure_ascii=False)
        return d

    # ------------------------------------------------------------ protocole SyncStore
    def list(self, entity: str, include_deleted: bool = False) -> list[dict]:
        self._check_entity(entity)
        q = f"SELECT * FROM {entity}"
        if not include_deleted:
            q += " WHERE deleted = 0"
        return [self._decode(entity, r) for r in self.conn.execute(q)]

    def get(self, entity: str, record_id: str) -> dict | None:
        self._check_entity(entity)
        r = self.conn.execute(f"SELECT * FROM {entity} WHERE id=?",
                              (record_id,)).fetchone()
        return self._decode(entity, r) if r else None

    def put(self, entity: str, record: dict, *, from_sync: bool = False) -> dict:
        self._check_entity(entity)
        rec = dict(record)
        rec.setdefault("id", uuid.uuid4().hex)
        if not from_sync:
            rec["updated_at"] = time.time()
            rec["device_id"] = self.device_id
            rec["dirty"] = 1
        rec.setdefault("deleted", 0)
        rec.setdefault("dirty", 0 if from_sync else 1)
        enc = self._encode(entity, rec)
        cols = ", ".join(enc)
        marks = ", ".join("?" * len(enc))
        self.conn.execute(
            f"INSERT OR REPLACE INTO {entity} ({cols}) VALUES ({marks})",
            tuple(enc.values()))
        self.conn.commit()
        return rec

    def delete(self, entity: str, record_id: str) -> None:
        cur = self.get(entity, record_id)
        if cur is None:
            return
        cur["deleted"] = 1
        self.put(entity, cur)          # tombstone estampillé (dirty, updated_at)

    def changed_since(self, entity: str, ts: float) -> Iterable[dict]:
        self._check_entity(entity)
        rows = self.conn.execute(
            f"SELECT * FROM {entity} WHERE updated_at > ?", (ts,))
        return [self._decode(entity, r) for r in rows]

    # ------------------------------------------------------------ réplication (sync.py)
    def dirty_records(self, entity: str) -> list[dict]:
        self._check_entity(entity)
        rows = self.conn.execute(f"SELECT * FROM {entity} WHERE dirty = 1")
        return [self._decode(entity, r) for r in rows]

    def mark_clean(self, entity: str, record_id: str, remote_rev: str | None) -> None:
        self._check_entity(entity)
        self.conn.execute(
            f"UPDATE {entity} SET dirty = 0, remote_rev = ? WHERE id = ?",
            (remote_rev, record_id))
        self.conn.commit()

    def sync_cursor(self, entity: str) -> float:
        r = self.conn.execute(
            "SELECT last_pulled_at FROM sync_state WHERE entity=?", (entity,)).fetchone()
        return r["last_pulled_at"] if r else 0.0

    def set_sync_cursor(self, entity: str, ts: float) -> None:
        self.conn.execute(
            "INSERT INTO sync_state (entity, last_pulled_at) VALUES (?, ?) "
            "ON CONFLICT(entity) DO UPDATE SET last_pulled_at = excluded.last_pulled_at",
            (entity, ts))
        self.conn.commit()
