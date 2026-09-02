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


class CorruptRecord(Exception):
    """Un enregistrement dont une colonne JSON est illisible — isolé, jamais fatal."""


def _device_id(state_dir: Path) -> str:
    """Identifiant stable de CET appareil (fichier local, créé une fois)."""
    f = state_dir / "device_id"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    did = uuid.uuid4().hex[:12]
    state_dir.mkdir(parents=True, exist_ok=True)
    f.write_text(did, encoding="utf-8")
    return did


class LocalStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        # WAL : l'UI ouvre une connexion NEUVE par requête et les jobs de fond écrivent depuis
        # leurs propres threads. En mode `delete` (défaut), une écriture longue BLOQUE les
        # lectures ; en WAL, lecteurs et rédacteur cohabitent — l'UI reste réactive pendant
        # l'import d'un gros pack.
        #
        # `busy_timeout` est laissé EXPLICITE mais ne change rien : Python le règle déjà à
        # 5000 ms via `sqlite3.connect(timeout=5.0)`. Mesuré — un scénario à 6 threads × 12
        # écritures ne produit AUCUN « database is locked », ni avant ni après ce changement.
        # WAL est donc une amélioration de réactivité, pas un correctif de panne observée.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA.read_text(encoding="utf-8"))
        self.device_id = _device_id(db_path.parent)
        self.corrupt: list[str] = []      # enregistrements sautés par `list()`, pour rapport
        self._colonnes: dict[str, set[str]] = {}   # cache des colonnes réelles par table

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

    def _check_columns(self, entity: str, record: dict) -> None:
        """Vérifie que chaque clé correspond à une VRAIE colonne de la table.

        `put()` interpole les noms de colonnes dans le SQL (impossible de les paramétrer) :
        le nom de table vient d'une liste blanche, mais les colonnes venaient des clés du
        dict de l'appelant. Une clé inattendue produisait au mieux un `OperationalError`
        cryptique, au pire du SQL arbitraire si un jour un appelant relayait des clés
        d'origine externe. Le schéma fait autorité.
        """
        connues = self._colonnes.get(entity)
        if connues is None:
            connues = {r[1] for r in self.conn.execute(f"PRAGMA table_info({entity})")}
            self._colonnes[entity] = connues
        inconnues = set(record) - connues
        if inconnues:
            raise ValueError(
                f"Colonne(s) inconnue(s) pour « {entity} » : {sorted(inconnues)}")

    def _decode(self, entity: str, row: sqlite3.Row) -> dict:
        """Décode les colonnes JSON. Lève `CorruptRecord` si l'une est illisible.

        L'appelant (`list`) ISOLE alors ce seul enregistrement : sans ça, une cellule
        corrompue (base éditée à la main, corruption disque, sérialisation d'une version
        antérieure) faisait échouer `list("decks")` en entier — l'utilisateur ne voyait plus
        AUCUN deck et croyait tout avoir perdu, alors qu'un seul était en cause.
        """
        d = dict(row)
        for col in _JSON_COLS[entity]:
            if isinstance(d.get(col), str):
                try:
                    d[col] = json.loads(d[col])
                except json.JSONDecodeError as e:
                    raise CorruptRecord(
                        f"{entity}/{d.get('id')} : colonne {col!r} illisible ({e})") from e
        return d

    def _encode(self, entity: str, record: dict) -> dict:
        d = dict(record)
        for col in _JSON_COLS[entity]:
            if col in d and not isinstance(d[col], str):
                d[col] = json.dumps(d[col], ensure_ascii=False)
        return d

    # ------------------------------------------------------------ protocole SyncStore
    def list(self, entity: str, include_deleted: bool = False) -> list[dict]:
        """Enregistrements lisibles de l'entité. Un enregistrement corrompu est SAUTÉ et
        signalé dans `self.corrupt`, jamais propagé — perdre une ligne ne doit pas rendre
        toute la collection invisible."""
        self._check_entity(entity)
        q = f"SELECT * FROM {entity}"
        if not include_deleted:
            q += " WHERE deleted = 0"
        out = []
        for r in self.conn.execute(q):
            try:
                out.append(self._decode(entity, r))
            except CorruptRecord as e:
                self.corrupt.append(str(e))
        return out

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
        self._check_columns(entity, enc)
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
