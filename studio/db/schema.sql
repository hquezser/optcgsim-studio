-- Schéma OFFLINE-FIRST du studio : chaque table synchronisable porte les mêmes colonnes
-- de réplication (updated_at, device_id, dirty, deleted, remote_rev) -> un seul moteur de
-- sync générique (storage/sync.py), last-write-wins par updated_at, tombstones (deleted=1,
-- jamais de DELETE physique avant confirmation de propagation).
--
-- Les clients mobiles (iOS/Android, sandboxés : impossibilité d'écrire dans le dossier du
-- sim) consomment EXACTEMENT ces entités via l'API : les decks et préférences vivent ici,
-- l'injection locale d'assets/decks n'est qu'un ADAPTATEUR desktop par-dessus.

CREATE TABLE IF NOT EXISTS profiles (
    id          TEXT PRIMARY KEY,          -- uuid4
    name        TEXT NOT NULL,
    prefs       TEXT NOT NULL DEFAULT '{}', -- JSON : cosmétiques actifs {kind: pack_id},
                                            --        langue, options d'affichage…
    updated_at  REAL NOT NULL,              -- epoch UTC (LWW)
    device_id   TEXT NOT NULL,
    dirty       INTEGER NOT NULL DEFAULT 1, -- 1 = modif locale non poussée
    deleted     INTEGER NOT NULL DEFAULT 0, -- tombstone
    remote_rev  TEXT                        -- révision serveur (opaque)
);

CREATE TABLE IF NOT EXISTS decks (
    id          TEXT PRIMARY KEY,           -- uuid4
    profile_id  TEXT NOT NULL REFERENCES profiles(id),
    name        TEXT NOT NULL,
    leader      TEXT NOT NULL,
    cards       TEXT NOT NULL,              -- JSON {card_id: qty} (50 cartes hors leader)
    tags        TEXT NOT NULL DEFAULT '[]', -- JSON ["ranked","tournoi-local","proxy"…]
    source      TEXT,                       -- url/clipboard/manuel
    updated_at  REAL NOT NULL,
    device_id   TEXT NOT NULL,
    dirty       INTEGER NOT NULL DEFAULT 1,
    deleted     INTEGER NOT NULL DEFAULT 0,
    remote_rev  TEXT
);
CREATE INDEX IF NOT EXISTS idx_decks_profile ON decks(profile_id, deleted);

-- Packs cosmétiques : la MÉTA se synchronise (nom, manifeste, hash) ; les binaires (images)
-- restent locaux en v1 (taille) — un client mobile voit "pack actif : X" sans les octets.
CREATE TABLE IF NOT EXISTS cosmetic_packs (
    id          TEXT PRIMARY KEY,           -- uuid4
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,              -- cards | playmat | cardback | background
                                            --   | translation | mixed
    local_path  TEXT,                       -- dossier local du pack (NULL sur mobile)
    manifest    TEXT NOT NULL DEFAULT '{}', -- JSON : fichiers, hashes, dimensions
    updated_at  REAL NOT NULL,
    device_id   TEXT NOT NULL,
    dirty       INTEGER NOT NULL DEFAULT 1,
    deleted     INTEGER NOT NULL DEFAULT 0,
    remote_rev  TEXT
);

-- Curseurs de synchronisation par entité (pull incrémental).
CREATE TABLE IF NOT EXISTS sync_state (
    entity          TEXT PRIMARY KEY,       -- profiles | decks | cosmetic_packs
    last_pulled_at  REAL NOT NULL DEFAULT 0,
    cursor          TEXT                    -- curseur serveur opaque (pagination)
);
