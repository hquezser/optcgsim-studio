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

### Import sélectif par type de carte

```bash
studio packs add <url> --only-type leader,event      # que les leaders + événements
studio packs add <url> --only don                    # que l'image de DON!!
studio packs add <url> --for-deck "Sanji P6K"        # que les cartes de ce deck
studio packs add <url> --leaders-only                # raccourci de --only-type leader
```

Sur une source **GitHub** (public ou privé avec token), le filtre s'applique **avant
téléchargement** : seuls les fichiers voulus sont récupérés. Mesuré sur le repo FR :
2,1 Go complet → 175 Mo en leaders, 257 Mo en événements. L'UI affiche l'estimation de
taille par type avant de lancer. Types reconnus via `card_types.json` (id → Leader /
Character / Event / Stage) ; DON traité comme sa propre catégorie.

Conseil d'organisation des dépôts (P8) : les images étant volumineuses, héberger
**plusieurs dépôts par famille** (alt-arts / traductions / playmats / cardbacks) plutôt
qu'un seul — on reste sous les limites GitHub (repos < ~1 Go) et chaque famille se met à
jour indépendamment. Le studio gère autant de sources suivies que voulu.

### Construire ces dépôts depuis tes liens (`studio repos build`) — commande MAINTENEUR

> **Hors surface end-user.** Cette commande n'est **pas** dans l'UI web (qui, elle, est
> destinée à tous les utilisateurs : importer/appliquer/restaurer). `repos build` est un
> outil du **créateur/mainteneur** qui produit les dépôts d'images privés — CLI uniquement.

Plutôt que d'organiser les dépôts à la main, on part des liens **déjà partagés** (GitHub,
Dropbox, Google Drive) et le studio **génère l'arborescence par famille**, prête à pousser :

```bash
studio repos build <lien-github|dropbox|drive> --out ~/optcgsim-repos --cards-as alt
studio repos build <lien-FR> --out ~/optcgsim-repos --cards-as translated   # cartes traduites
```

Chaque fichier est classé (via `classify_rel` + `cardmeta`) et routé :

- **cartes** (dont **DON!! alternatif**, qui est un reskin de carte, pas un dos de carte) →
  dépôt de la famille passée (`--cards-as alt` → `cards-alt/`, `translated` →
  `translations/`), **sous-classées par type** : `Leaders/Cards/<SET>/<ID>.png`,
  `Events/…`, `Don/Cards/Don/…`, etc. → l'import granulaire `--only-type` marche direct sur
  le dépôt poussé, et une famille trop lourde se scinde en déplaçant un simple sous-dossier
  de type ;
- `CardBacks/` (vrais dos de carte uniquement) → `cardbacks/`, `Playmats/` + fonds →
  `playmats/`, `TRANSLATION.txt` → `translations/`.

**Une source qui mélange plusieurs variantes de la même carte** (ex. un repo de traduction
qui contient à la fois des cartes « classiques » et des cartes « alternatives » traduites) :
le nom canonique retire les suffixes parasites (`_alt`, `_OVERRIDE`…) pour produire UN fichier
par id — deux variantes traitées dans le MÊME `build()` entrent donc en collision (rapportée,
dernière gagnante, perte silencieuse si on ne regarde pas le rapport). Scinder avec
`--path-prefix` : un `build()` par variante, chacun scopé à son sous-dossier et avec un
`--cards-as` distinct, préserve les deux :

```bash
studio repos build <lien-fr> --out ~/optcgsim-repos --cards-as translated-fr-classic --path-prefix FR_classique --lang fr
studio repos build <lien-fr> --out ~/optcgsim-repos --cards-as translated-fr-fullart --path-prefix FR_full_art --lang fr
```

(adapter les `--path-prefix` aux vrais noms de dossiers de la source — `studio repos build`
sans `--path-prefix` d'abord, en lisant les non-classés/collisions du rapport, aide à les
repérer). Chaque configuration est mémorisée séparément : `studio repos update --out …`
rejoue les deux.

Le préfixe ne restreint QUE les cartes/DON!! (seules catégories canonicalisées par id, donc à
risque de collision) — un `TRANSLATION.txt`, des playmats ou des dos de carte à la racine de
la source (hors des deux sous-dossiers) sont inclus dans **chaque** build scopé, pas besoin
d'un 3ᵉ appel dédié.

**`--lang`** découple la LANGUE du texte de traduction de la VARIANTE d'art choisie
(`--cards-as`) — deux axes orthogonaux. Sans `--lang`, la traduction suit un alias fixe
(`translations`) : une future langue (ES…) écraserait la FR. Avec `--lang fr` sur les DEUX
builds ci-dessus, leur `TRANSLATION.txt` converge dans un seul dépôt `translations-fr/` (pas
dupliqué par variante d'art) ; une langue future (`--lang es`) va dans `translations-es/`,
sans jamais toucher au FR.

Sources : **Dropbox** (dossier partagé → zip) est téléchargé entier ; **Google Drive** est
géré pour les **fichiers/zip partagés** (« tout le monde avec le lien ») — un *dossier* Drive
n'a pas d'export zip public, partage-le en `.zip`. **GitHub** est téléchargé entier PAR
DÉFAUT, mais dès que `--path-prefix` est actif, `repos build`/`update` passent en **fetch
sélectif** (API Tree + `raw.githubusercontent.com` fichier par fichier — la même mécanique
que l'import P7) : seuls les fichiers du périmètre sont transférés, au lieu du dépôt entier.
Mesuré : jusqu'à 98 % d'économie sur un gros dépôt scopé à un seul sous-dossier — et un
fichier corrompu en route n'affecte plus que CE fichier (repli sur un zip monolithique =
toute l'extraction plante). Le contenu passe par le CDN `raw.githubusercontent.com`, pas
l'API REST `api.github.com` (plafonnée à **60 requêtes/heure sans authentification**, quel
que soit le dépôt — public ou privé ; un dossier de quelques centaines de cartes l'épuiserait
en une seule commande) ; l'API REST ne sert plus qu'à lister l'arborescence (1 requête) et de
repli si le CDN échoue pour un chemin. **Configure un token GitHub** même pour un dépôt
public (`studio config set-github-token <PAT>`, aucune permission particulière requise) : ça
fait passer la limite à 5000/heure et sécurise les commandes suivantes. Le token configuré
est réutilisé automatiquement. Chaque dépôt généré reçoit un `git init`, un `MANIFEST.json`
(comptes par type, tailles) et un `README`. Le studio **ne pousse rien** : il affiche la
recette `git remote add … && git push` à lancer toi-même. Un dépôt qui dépasse ~900 Mo est
signalé (scinde-le). Ces dépôts d'images restent **privés** (tu n'es pas l'ayant droit).

**Téléchargement corrompu** (coupure réseau en cours de route, CRC invalide) : si SEULS
quelques fichiers d'une archive GitHub sont en défaut, le studio ne rejette pas tout le
téléchargement déjà fait — il **patche uniquement ces fichiers** (même mécanique que le
fetch sélectif ci-dessus : CDN d'abord, API Contents en repli) au lieu de retélécharger
l'archive entière. Ce n'est que si le
patch est impossible (source non GitHub) ou échoue à son tour que `repos build`/`update`
retentent un téléchargement complet (3 tentatives) avant d'abandonner avec un message clair
— plutôt qu'une trace Python brute. `--path-prefix` sur une source GitHub réduit aussi
fortement l'exposition à ce problème sur les gros dépôts (moins de données transférées).

**Mettre à jour à chaque sortie de set**, sans retaper les liens :

```bash
studio repos update --out ~/optcgsim-repos
```

Rejoue exactement les sources de chaque `repos build` précédent (mémorisées dans
`.repos-build.json`, à la racine de `--out` — hors des dépôts eux-mêmes, jamais poussé) et
affiche un **diff par dépôt** : fichiers ajoutés / modifiés (par empreinte sha1) / orphelins
(disparus de la source — jamais supprimés automatiquement, juste signalés). Sert de base au
message de commit (« ajoute OP15, corrige 2 alt-arts OP14 »). Ré-exécuter `repos build`
directement sur un `--out` déjà construit est aussi sûr (idempotent) — `update` évite juste
de retaper les sources.

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
