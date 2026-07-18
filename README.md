# optcgsim-studio

Plateforme de gestion de l'expérience de jeu OPTCGSim : **QoL, personnalisation esthétique,
importation universelle de decklists, synchronisation multi-appareils** (desktop aujourd'hui,
iOS/Android demain).

## Interface web (`studio ui`)

```bash
studio ui                 # lance l'UI locale (http://127.0.0.1:8770) et ouvre le navigateur
```

Interface **zéro dépendance** : servie par la bibliothèque standard Python (aucun Node, aucun
`npm install`, aucun build). On glisse un `.zip` de thème, on colle une URL ou une decklist,
on applique/restaure en un clic. Le crochet d'adoption : chaque pack affiche **combien de
cartes de _tes_ decks il habille** (« ce pack couvre 38/51 cartes de ton deck »). Écoute sur
`127.0.0.1` uniquement, jamais exposée au réseau.

L'UI parle à une **API JSON** (`studio/api/server.py`) qui est le même contrat que consommera
un futur client Next.js/Tauri (mobile) — la logique reste côté Python (voir
`frontend/README.md` pour la structure portable).

> **Positionnement dans l'écosystème.** Trois projets frères, finalités disjointes :
> [optcgsim-haki](https://github.com/hquezser/optcgsim-haki) = décision *in-match* (overlay,
> lecture de logs, **aucune modification du jeu** — promesse de son README, intacte) ;
> `optcgsim-rogue-lab` = analytique *post-match* ; `optcgsim-studio` = expérience de jeu.
> Le studio, lui, modifie des fichiers du sim — **cosmétiques uniquement**, en local, opt-in,
> avec restauration intégrale. Cette séparation de repos est délibérée : chaque outil tient
> sa promesse propre.

## Structure des dossiers

```
optcgsim-studio/
├── studio/                     # backend Python (aucune dépendance externe)
│   ├── gamepaths.py            # découverte cross-OS de l'installation du sim
│   ├── assets/
│   │   └── manager.py          # pilier 1 : hot-swap cosmétique sûr (backup+manifeste)
│   ├── decks/
│   │   └── importer.py         # pilier 2 : import universel de decklists
│   ├── storage/
│   │   ├── base.py             # pilier 3 : protocole SyncStore (l'abstraction)
│   │   ├── local.py            #   SQLite offline-first (mode déconnecté, défaut)
│   │   ├── remote.py           #   backends cloud : contrat REST/Supabase + FakeRemote
│   │   └── sync.py             #   moteur LWW + tombstones (générique par entité)
│   ├── db/schema.sql           # entités synchronisables (colonnes de réplication)
│   └── cli.py                  # studio assets|decks|sync
├── frontend/                   # voir frontend/README.md (structure Tauri/RN-ready)
└── tests/                      # 41 tests (fausse install tmp, convergence 2 appareils)
```

## Pilier 1 — Assets cosmétiques (`studio assets …`)

Cartographie établie par **inspection réelle** (macOS, sim 1.41b) : tous les assets visés
sont des **fichiers libres** — aucun bundle Unity à dépaqueter.

| Asset | Emplacement réel | Format |
|---|---|---|
| Image de carte | `StreamingAssets/Cards/<SET>/<ID>.png` (+ `<ID>_small.jpg`) | PNG 480×671 |
| Cartes récentes | `<persistant>/<version>/Cards/<ID>.jpg` (cache téléchargé) | JPEG |
| Tapis de jeu | `StreamingAssets/Playmats/<Nom>.png` | PNG |
| Dos de cartes | `StreamingAssets/CardBacks/CardBack{Regular,Don}.png` | PNG |
| Fonds | `StreamingAssets/{background,deckeditbackground}.jpg` | JPEG |
| Localisation | `StreamingAssets/TRANSLATION.txt` | `Clé=Valeur` |

Garanties du gestionnaire : whitelist stricte (jamais de code, extensions verrouillées),
validation des images par magic-bytes + dimensions (un script déguisé en PNG est rejeté),
écriture atomique, **backup pristine + manifeste avant tout swap**, `restore-all` intégral,
fusion non destructive des traductions, **aucune élévation de privilèges** (si le dossier
n'est pas écrivable, on explique — on ne sudo jamais).

Réalités OS documentées : sur macOS l'app est signée — swapper invalide la signature (app
déjà autorisée : OK ; `restore-all` sinon). Une mise à jour du sim écrase les swaps → les
packs restent côté studio et se ré-appliquent (`apply-pack`). Windows/Linux : chemins Unity
standard, `verified=False` tant que non confirmés sur machine réelle.

```bash
studio assets inventory                 # ce que TON installation expose (61 sets…)
studio assets apply-mirror ~/Theme --dry-run   # thème calqué sur StreamingAssets (Themer & co)
studio assets apply-mirror ~/Theme      # applique après prévisualisation
studio assets apply-pack ~/MonPack      # layout : cards/, playmats/, cardback.png, translation.txt
studio assets status                    # active / overwritten (màj sim) / original
studio assets restore-all
```

**`apply-mirror`** absorbe les thèmes distribués comme un miroir de `StreamingAssets` (le
modèle du site [optcgsimthemer.com](https://www.optcgsimthemer.com) : Playmats, Menus,
CardBacks, Don, Cards). Règle unique et sûre : un fichier du thème n'est appliqué que si le
**même chemin relatif existe déjà** dans le jeu et partage son format — jamais de création
de fichier inconnu. Couvre d'office les catégories qu'`apply-pack` ne gérait pas
(`Cards/Don/Don.png`, fonds). Les `.txt` sont renvoyés vers la fusion de traduction.
`--dry-run` prévisualise (rien écrit) ; `restore-all` annule tout.

## Pilier 2 — Import universel de decklists (`studio decks …`)

Tous les formats communautaires → format natif du sim (`1xOP01-001`, leader en tête),
validation des règles (1 leader + 50 cartes, ≤ 4 exemplaires), écriture directe là où le
sim lit ses decks ET enregistrement en base synchronisable (tags d'environnement).

```bash
studio decks import --clipboard --name "Sanji P6K" --tags ranked,op17
studio decks import --url https://…     # extraction générique best-effort
studio decks import --file liste.txt
studio decks import-pack <dossier|zip|url>   # collection entière (deckpack.json)
studio decks list
```

**Pack de decks** (`deckpack.json`) : importe une collection nommée d'un coup (« Meta OP16 »,
« Rogue decks de Trecore »). Manifeste propre au studio, chaque deck résolu par le moteur
d'import ci-dessus (`text` inline, `file` dans le pack, ou `source_url`) ; un deck en échec
n'interrompt jamais les autres (rapport importés / échecs).

```json
{"name": "Meta OP16", "author": "Trecore",
 "decks": [{"name": "Sanji Red", "tags": ["meta"], "file": "decks/sanji.txt"},
           {"name": "Kid", "tags": ["meta"], "source_url": "https://..."}]}
```

Chemin garanti : le bouton « Export OPTCGSim » des sites (NakamaDecks, EgmanEvents,
LimitlessTCG) produit le format natif → `--clipboard`. L'URL directe tente une extraction
générique (bloc natif embarqué, sinon paires qty×id) et échoue proprement avec ce conseil —
choix assumé : pas de scraper par site, leurs DOM changent, le format natif non.

### Normalisation de packs hétérogènes (`studio packs add`)

Tous les packs communautaires ne suivent pas le layout miroir. `packs add` ingère une source
(dossier, `.zip`, URL GitHub ou Dropbox partagé — zip-slip refusé) et la **normalise** en
pack canonique de bibliothèque, par classification fichier par fichier :

- sous une racine miroir (`Cards/`, `Playmats/`…) → chemin préservé (Themer, Dropbox) ;
- nom = id de carte, suffixes parasites retirés (`_OVERRIDE` du patch FR, `_alt`…) →
  `Cards/<SET>/<ID>.png` ;
- `.txt` en `Clé=Valeur` → traduction ; noms de dos/tapis/fond reconnus → catégorie ;
- **tout le reste → rapporté « non classé » avec raison** (jamais de perte silencieuse).

```bash
studio packs add ~/theme.zip                        # normalise + enregistre en bibliothèque
studio packs add https://github.com/Sparklight-TL/OPTCGSim_FR   # traduction + cartes FR
studio packs list                                   # bibliothèque (nom, type, couverture)
studio packs show <nom>                             # détail + état appliqué
studio packs apply <nom> --dry-run                  # prévisualise
studio packs apply <nom> --only cards,translation   # applique (filtre par catégorie)
studio packs remove <nom>                           # restaure les originaux de CE pack
```

`add` normalise, enregistre en bibliothèque (`~/.optcgsim-studio/packs/`, table
`cosmetic_packs` synchronisable) et **n'applique rien**. `apply` route le pack miroir vers
`apply_mirror` + fusionne la traduction éventuelle (jamais d'écrasement de `TRANSLATION.txt`).
**Collisions** entre packs : le dernier appliqué gagne, avec avertissement — mais le backup
reste toujours l'ORIGINAL du jeu, donc `remove`/`restore-all` ramènent à l'état d'usine.
`remove` ne restaure que les cibles encore tenues par ce pack (une cible reprise depuis par
un autre pack n'est pas défaite).

### Sources suivies & mises à jour

```bash
studio packs add --follow https://github.com/Sparklight-TL/OPTCGSim_FR
studio packs update [nom]     # re-télécharge les packs suivis, diffe, ré-applique le delta
studio packs reapply          # ré-applique les packs écrasés par une mise à jour du sim
```

Le manifeste garde une empreinte (sha1) par fichier : `update` re-télécharge la source
suivie, calcule le delta (modifiés/retirés) et **ne ré-applique que si le pack est déjà
actif**. Indispensable pour le repo FR (régénéré par CI → sans suivi la traduction se périme
en silence). `reapply` détecte les swaps passés à l'état `overwritten` (typiquement après
une mise à jour du simulateur qui réécrit `StreamingAssets`) et réinjecte les packs depuis
la bibliothèque, sans re-téléchargement.

## Pilier 3 — Synchronisation multi-appareils (`studio sync`)

Offline-first : SQLite local par défaut, cloud en opt-in — **même protocole** (`SyncStore`),
la logique métier ne voit pas la différence. Réplication générique par entité (profils,
decks, packs cosmétiques) : last-write-wins par `updated_at`, tombstones (pas de
résurrection), curseur de pull incrémental. Convergence deux-appareils couverte par tests.

Les clients mobiles sandboxés (impossible d'écrire dans les fichiers d'une autre app)
consomment les MÊMES entités via l'API : l'injection locale (pilier 1) et l'écriture des
.txt (pilier 2) ne sont que des **adaptateurs desktop** par-dessus le store. Backend :
contrat REST minimal documenté dans `storage/remote.py`, ou Supabase (tables miroir + RLS
`user_id = auth.uid()` via PostgREST = le même contrat sans serveur à écrire). Les binaires
des packs ne se synchronisent pas en v1 (seule la méta circule).

```bash
studio sync                              # sans --url : mode déconnecté, explique quoi faire
studio sync --url https://api.exemple --token <jeton>
```
