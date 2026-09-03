# optcgsim-studio — Guide de développement

## Écosystème OPTCGSim — tu es ici

4 projets frères dans `optcgsim-ecosystem/`, finalités disjointes. Voir le
[README de l'écosystème](../README.md) pour la chaîne complète et la table des liens.

| Repo | Rôle | Statut |
|---|---|---|
| `optcgsim-deckpacks` | **Le format** : spec `deckpack.json`, schéma, validateur (l'arbitre). | local |
| `optcgsim-deckpacks-data` | **Les données** : scrapers + un pack par tournoi. | local |
| `optcgsim-deckpacks-library` | **La vitrine** : générateur de site statique, rampe d'accès vers le simulateur. | local |
| **`optcgsim-studio`** ← ici | **Le consommateur** : `studio decks import-pack`, écrit les decks dans le jeu. | publié : github.com/hquezser/optcgsim-studio |

Les liens inter-projets sont des chemins **relatifs entre voisins** : les quatre dépôts
doivent rester frères dans ce dossier.

Hors écosystème, rangés dans `../../draft-optcgsim-projects/` : `optcgsim-haki`,
`optcgsim-haki-public`, `optcgsim-rogue-lab`. Seule dépendance résiduelle —
`optcgsim-studio/scripts/refresh_cardmeta.py` lit `card_stats.json` depuis
`optcgsim-haki-public`, mais uniquement pour régénérer une table vendorisée (maintenance,
pas exécution).

**Périmètre de CE repo** : modifie des fichiers du sim — **cosmétiques uniquement**
(images/traductions/tapis), toujours réversible (`assets restore-all`), jamais le moteur de
jeu. Zéro dépendance externe (stdlib only, même pour le serveur HTTP de l'UI).

**Mémoire de session** : `~/.claude/projects/-Users-hugoq-playground-optcgsim-studio/memory/MEMORY.md`.

**Plan détaillé** : `docs/PLAN-import-packs.md` (import de packs, P0→P10 — historique complet des
décisions et incidents réels résolus ; à consulter avant de re-décider quoi que ce soit sur ce
sujet).

## Build & Test

La CI (`.github/workflows/ci.yml`) rejoue la suite sur Python 3.10 → 3.13, plus macOS et
Windows (Windows informatif tant que les chemins n'y sont pas confirmés), vérifie que
`studio --help` répond et qu'aucun fichier sensible n'est versionné.

```bash
python3 -m pytest -q          # suite complète (stdlib + pytest, aucune autre dépendance)
pip install -e .               # installer en dev (editable) — expose la commande `studio`
studio ui                      # lance l'UI locale (http://127.0.0.1:8770)
```

Convention stricte : zéro dépendance externe en dehors de `pytest` (dev only). Ne pas
introduire de paquet tiers dans `studio/` sans en discuter — c'est un choix délibéré (l'UI
tourne sur `http.server` de la stdlib, pas de Node/npm/build).

## Architecture

- `studio/` — package Python
  - `cli.py` — entry point `studio` (assets/packs/decks/repos/ui/sync/config)
  - `assets/` — **pilier 1** : cosmétiques
    - `manager.py` — hot-swap sûr (whitelist, magic-bytes, backup pristine, restore)
    - `packlib.py` — normalisation multi-sources (Themer/Dropbox/GitHub/Drive) en pack canonique
    - `repobuild.py` — **P8+** : construit des dépôts d'images par famille depuis des sources
      hétérogènes (`repos build`/`repos update`), CLI-only (outil mainteneur, jamais dans l'UI —
      voir mémoire `studio-ui-vs-maintainer-boundary`)
    - `collections.py` — **P10** : format `collection.json` (variantes/compléments) pour
      importer plusieurs dépôts liés en un geste via l'UI
    - `slots.py` — **sélecteur d'emplacements** : le jeu n'a qu'UNE case par emplacement
      (un `Cards/Don/Don.png`, un tapis par couleur) là où un dépôt d'alt-arts en propose des
      centaines ; `apply_mirror` n'écrivant jamais un fichier inconnu du jeu, c'était le nom
      de fichier qui tranchait. Ce module nomme les emplacements, liste les candidats de la
      bibliothèque et persiste le choix (`<state>/slots.json`) pour qu'une ré-application de
      pack ne l'écrase pas en silence. N'écrit jamais lui-même : passe par `AssetManager`.
    - `sourcefetch.py` — fetch sélectif GitHub (Tree API + CDN raw.githubusercontent.com)
    - `cardmeta.py` — table id→type de carte (Leader/Character/Event/Stage), vendorisée
  - `decks/` — **pilier 2** : import universel de decklists (`importer.py`, `deckpack.py`)
  - `storage/` — **pilier 3** : sync offline-first (`local.py` SQLite, `remote.py`, `sync.py`)
  - `api/` — serveur JSON + UI web (`server.py`, `static/index.html`, `jobs.py` pour les
    opérations longues en tâche de fond)
  - `config.py` — secrets locaux (token GitHub), jamais renvoyés en clair par l'API
  - `nettls.py` — contournement cert SSL macOS (python.org sans root certs)
- `docs/PLAN-import-packs.md` — plan détaillé du chantier « import de packs » (P0→P10)
- `frontend/` — structure portable pour un futur client Tauri/React Native (voir son README)
- `tests/` — pytest, zéro accès réseau réel (tout mocké), zéro écriture dans
  `~/.optcgsim-studio` réel (state_dir paramétrable, toujours `tmp_path` en test)

## Garde-fous (ne jamais contourner)

- Écriture dans le jeu : uniquement via `AssetManager._swap` (whitelist stricte, magic-bytes +
  dimensions, atomique, backup pristine + manifeste, `restore-all` intégral).
- Aucune élévation de privilèges : si un dossier n'est pas écrivable, on l'explique, jamais de
  `sudo`.
- `repos build`/`repos update` restent **CLI-only** : produire un dépôt d'images est un geste
  de mainteneur, pas une action end-user — ne pas les exposer dans l'UI web sans qu'on te le
  redemande explicitement.
- Tests : jamais de mutation du vrai `~/.optcgsim-studio/` ou de la vraie installation du jeu —
  toujours via `state_dir`/`GameInstall` paramétrés sur `tmp_path`.
- **Zéro dépendance externe** : `tests/test_no_external_deps.py` le vérifie mécaniquement (un
  `import` hors stdlib au niveau module fait échouer la suite). Ce n'est plus une convention
  qu'on peut oublier. `certifi` est la seule exception tolérée, en import PARESSEUX avec repli.
- **Tout contenu tiers est hostile** (noms de packs/decks, `deckpack.json`, `collection.json`
  distant). Dans l'UI : passer par `esc()` avant toute interpolation, et jamais de
  `onclick="f('${valeur}')"` — délégation d'évènements + `data-*`. Côté Python : un chemin
  venant d'un manifeste se résout puis se vérifie avec `is_relative_to`.
- **Un correctif de bug arrive avec un test vérifié EN ÉCHEC sur le code d'avant.** Un test
  écrit après coup qui passe des deux côtés ne prouve rien (cf. `CHANGELOG.md`, section
  « connu, non corrigé », pour les cas où ça n'a pas été possible).
