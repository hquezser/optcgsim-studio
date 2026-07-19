# PLAN — Import ergonomique de packs communautaires (cartes + customs)

> **AVANCEMENT (2026-07-18)** — P0→P4 FAITS (commits 9405a85, c5f587b, a10733e, 66c9b33,
> 0e03ad8) + jobs de fond/progression (71e064f). 90 tests verts. `studio ui` fonctionne de
> bout en bout : add/apply/remove/update/reapply, dropzone, couverture de decks, jobs qui
> survivent à la fermeture de l'onglet. **P5-P7 ci-dessous = chantiers futurs demandés par
> l'utilisateur (2026-07-18)**, pas encore commencés — reconnaissance faite, décisions
> ouvertes à trancher avant d'implémenter (contrairement à P0-P4, ces trois-là ont de vraies
> questions produit, pas seulement techniques).
>
> Objectif : un utilisateur pointe une SOURCE (zip, dossier, URL) et le studio fait le
> reste — analyse, normalisation, prévisualisation, application, mise à jour, restauration.
> Zéro réorganisation manuelle.
>
> Fondé sur TROIS sources réelles inspectées (2026-07-17/18) :
>
> | Source | Contenu | Layout observé |
> |---|---|---|
> | **optcgsimthemer.com** | Playmats, Menus, CardBacks, Don, Cards + interface de création | zip = **miroir direct de StreamingAssets** (« copy into StreamingAssets, overwriting ») |
> | **Dropbox « Alt Cards Jon »** | alt-arts EN | `Cards/<SET>/<ID>.png + <ID>_small.jpg` (nommage sim, imbriqué par set) + `Custom Cards`/`Extra Alts` (non vérifiés) |
> | **GitHub Sparklight-TL/OPTCGSim_FR** | traduction FR + cartes FR | `TRANSLATION.txt` (format officiel) + `FR_classique\|FR_full_art/<SET>/<ID>_OVERRIDE.png` ; régénéré par CI |
>
> Réalité locale re-vérifiée : « Menus » du Themer = `background.jpg` + `deckeditbackground.jpg`
> (déjà gérés) ; « Don » = `Cards/Don/Don.png` (id hors gabarit CARD_ID → cas spécial à
> whitelister). Le guide Themer confirme le modèle d'installation = écrasement de fichiers
> EXISTANTS de StreamingAssets — exactement la primitive `_swap` du manager.

## Chantier P0 — Primitive « miroir StreamingAssets » (la clé de voûte)

Une seule extension du manager absorbe TOUTES les catégories Themer (menus, Don, cards,
playmats, cardbacks, et toute catégorie future) sans énumération :

```
apply_mirror(pack_root) :
    pour chaque image du pack (chemin relatif R, ex. Playmats/Blue.png, Cards/Don/Don.png) :
        cible = streaming_assets / R
        si la cible EXISTE et même format -> _swap (backup+manifeste+atomique, inchangé)
        sinon -> rapport « ignoré : pas de cible » (JAMAIS de création de fichier)
```

- Réutilise intégralement les garde-fous existants (`_guard_target` borne déjà à
  StreamingAssets/persistent, magic-bytes, symlinks, extensions, atomicité, backups).
- « Jamais de création » = on ne peut pas introduire de fichier inconnu du jeu — le pack le
  plus malveillant ne peut que re-skinner de l'existant, réversible par `restore_all`.
- Cas spécial levé au passage : `Cards/Don/Don.png` (et tout id hors gabarit) marche par
  chemin-miroir sans toucher au gabarit CARD_ID d'`apply_card`.
- Tests : pack miroir synthétique (don + menu + carte + fichier inconnu → ignoré + rapporté).

Effort : petit (~½ session). À faire EN PREMIER : le Themer devient alors 100 % supporté,
et le « pack canonique » interne de la packlib devient tout simplement… le layout miroir.

## Chantier P1 — Normaliseur de packs (`studio/assets/packlib.py`)

Transforme n'importe quelle arborescence en **pack miroir** stocké dans la bibliothèque du
studio (`~/.optcgsim-studio/packs/<nom>/`), enregistré en DB (`cosmetic_packs`, déjà au
schéma) avec manifeste (source, date, hashes, rapport).

1. **Ingestion** : dossier, zip (protection zip-slip), URL GitHub (zip de branche), URL
   Dropbox partagée (`?dl=1`), URL de zip direct. Streaming disque (packs = centaines de Mo).
2. **Détection du layout**, dans l'ordre :
   a. *miroir StreamingAssets* (racines `Cards/`, `Playmats/`, `CardBacks/`… reconnues) →
      pris tel quel — c'est le cas Themer, zéro transformation ;
   b. *nommage par id* : scan récursif de toutes les images, id extrait du NOM DE FICHIER
      (gabarit CARD_ID), sous-dossiers indifférents ; suffixes normalisés : `_small`
      (miniature), `_OVERRIDE` (patcher FR — retiré), `_alt/_v2` (variantes listées) ;
   c. *fichiers spéciaux* : `.txt` au format Clé=Valeur → traduction ; noms contenant
      « cardback », « playmat »/nom de tapis connu, « background » → catégorie dédiée ;
      `Don.png` → `Cards/Don/`.
3. **Rapport structuré** (consommé par CLI et frontend) : reconnus par catégorie,
   présents/absents de l'install, non-classés AVEC raison. Jamais de silence.

Acceptation : le zip Themer, le zip Dropbox de Jon et le clone du repo FR se normalisent
sans retouche manuelle (tests sur arborescences synthétiques reproduisant les 3 layouts).

## Chantier P2 — CLI bibliothèque de packs

```
studio packs add <zip|dossier|url> [--name X]     # normalise + rapport, n'applique rien
studio packs list / show <pack>                   # bibliothèque, contenu, état, collisions
studio packs apply <pack> [--only cards|translation|playmats|...] [--dry-run]
studio packs remove <pack>                        # restaure ce que CE pack avait posé
```

- `apply` affiche le résumé avant d'écrire (« 320 cartes, 214 clés de traduction ») ;
  `--dry-run` s'arrête là. Rappel « ferme le sim » si processus détecté.
- **Collisions entre packs** : dernier appliqué gagne + avertissement ; `show` dit par
  fichier quel pack est actif ; le backup pristine reste celui de l'ORIGINAL (garanti).

## Chantier P3 — Sources suivies (mises à jour)

```
studio packs add --follow <url>       # mémorise la source (GitHub/Themer/Dropbox)
studio packs update [pack]            # re-télécharge, diffe les hashes, ré-applique le delta
studio packs reapply                  # après màj du sim (états overwritten/missing du status)
```

Indispensable pour le repo FR (régénéré par CI : sans suivi, la traduction se périme en
silence) et utile après chaque mise à jour du sim (qui écrase les swaps).

## Chantier P4 — Frontend (l'ergonomie cible)

Page « Cosmétiques » branchée sur les MÊMES rapports JSON que le CLI :
1. **Dropzone** zip/dossier + champ URL → analyse → préview avant/après (image actuelle du
   sim vs pack), toggles par catégorie, liste des non-classés.
2. **Couverture de MES decks** : croisement avec la DB — « ce pack couvre 38/51 cartes de
   ton deck Sanji P6K » ; tri des packs par pertinence.
3. Badges d'état (actif / partiel / écrasé par màj sim), boutons apply/update/restore.
4. (plus tard, inspiré du créateur Themer) : composition de thème dans NOTRE frontend —
   hors périmètre de ce plan.

## Chantier P5 — Catalogue communautaire — ABANDONNÉ (2026-07-19)

> **Décision** : abandonné. Un catalogue LOCAL générique fait doublon avec l'existant —
> P7 importe déjà n'importe quelle URL GitHub (public/privé) sélectivement, et P3 `--follow`
> + `update` mémorise déjà la source de chaque pack et la re-télécharge (la bibliothèque EST
> déjà la liste des sources de l'utilisateur). La seule valeur non-redondante d'un catalogue
> serait le PARTAGE (registre distant), mais l'utilisateur préfère investir l'effort sur les
> dépôts GitHub privés d'images custom (P8) et sur la publication d'un format contributif
> (P9). Contenu original conservé ci-dessous pour mémoire.

Objectif (abandonné) : une page « Découvrir » listant des sources connues, un clic = `packs
add` (le flux job existant, inchangé).

1. **Catalogue embarqué** : `studio/api/catalog.json` — liste éditée par le studio (pas de
   scraping), seedée avec les 2 sources déjà validées comme automatisables :
   - Dropbox « Alt Cards Jon » (fonctionne tel quel via `packlib.ingest`) ;
   - GitHub `Sparklight-TL/OPTCGSim_FR` (idem, `--follow` recommandé — régénéré par CI).
   Chaque entrée : `{name, url, kind, description, maintainer}`.
2. **optcgsimthemer.com — cas particulier** : pas d'URL de zip statique (le site génère un
   thème à la demande via `/create` puis bouton Download). Le catalogue ne peut donc que
   **lier vers le site**, pas déclencher un `packs add` direct — à documenter clairement
   dans l'entrée catalogue (pas une limitation du studio, une limitation de la source).
3. **API** : `GET /api/catalog` → catalogue embarqué. Décision ouverte : faut-il en plus
   permettre à l'utilisateur d'ajouter SES PROPRES entrées (table locale
   `catalog_entries`, éditable via CLI/UI) ? Je recommande OUI mais PAS de « registre
   distant » auto-téléchargé par défaut (supply-chain : on ne veut pas qu'un studio
   installé aujourd'hui se mette à faire confiance à une liste tierce qui change demain,
   sans action explicite de l'utilisateur).
4. **UI** : section « Découvrir » réutilisant les cartes de pack existantes (mêmes
   boutons Ajouter/Appliquer) + badge de provenance (source, mainteneur) — transparence
   sur l'origine tierce du contenu.

Effort : ~½ session (le gros du travail — ingestion, jobs, UI de pack — existe déjà).

## Chantier P6 — Format « pack de decks » (import groupé de decklists)

Objectif : importer d'un coup une collection nommée (« Meta OP16 », « Rogue decks de
Trecore ») au lieu d'un deck à la fois.

1. **Manifeste `deckpack.json`** (nouveau format, PROPRE au studio — pas de scraping de
   tier-list, cohérent avec le choix déjà fait pour `importer.py`) :
   ```json
   {"name": "Meta OP16", "author": "Trecore",
    "decks": [
      {"name": "Sanji Red", "tags": ["meta","op16"], "source_url": "https://..."},
      {"name": "Rogue Zoro", "tags": ["rogue"], "text": "1xOP01-001\n4x..."}
    ]}
   ```
   Chaque entrée résolue via `importer.from_url` OU `parse_text` (texte inline) — le
   moteur d'import existant, sans modification.
2. **Ingestion** : `packlib.ingest()` réutilisé tel quel (dossier/zip/URL) pour récupérer
   le manifeste (+ éventuels `.txt` embarqués dans un zip).
3. **Rapport structuré** (même philosophie que `PackReport`) : `{imported: [...], failed:
   [{name, reason}]}` — jamais un deck en échec ne bloque silencieusement les autres.
4. **CLI/API** : `studio decks import-pack <source>` / `POST /api/deckpacks/add` (job de
   fond — résoudre N URLs peut prendre quelques secondes, réutilise le JobManager).
5. **Décision ouverte** : qui écrit ces manifestes au départ ? Recommandation : curation
   MANUELLE (par toi ou la personne qui maintient la source), pas de scraper de tier-list
   automatique — même principe que « pas de scraper par site » déjà retenu pour les
   decklists individuelles (les pages changent, le format manifeste non).

Effort : ~1 session (nouveau module, mais s'appuie entièrement sur `importer.py` existant).

## Chantier P7 — Import sélectif (disque ET bande passante quand la source le permet)

> **Reconnaissance faite (2026-07-18)** avant d'écarter le fetch sélectif fichier-par-fichier —
> conclusion différente par source, avec preuves :
>
> | Source | Explorer la structure sans tout télécharger | Fetch sélectif par fichier |
> |---|---|---|
> | **GitHub** | ✅ Tree API (`git/trees/<branche>?recursive=1`), 1 requête, sans auth, avec tailles exactes | ✅ `raw.githubusercontent.com/<owner>/<repo>/<branche>/<chemin>` par fichier |
> | **Dropbox** | ⚠️ Existe (`list_shared_link_folder_entries`, hrefs par fichier) mais endpoint interne | ❌ Refuse les appels scriptés (`403`, protection session/CSRF) sans navigateur automatisé complet |
> | **Themer** | ❌ zip généré à la demande, pas de dossier à parcourir | ❌ non applicable |
>
> Test réel sur le repo FR : **2,2 Go (repo complet) → 39 Mo (un seul set OP01), soit 98 %
> d'économie** — pas qu'un gain marginal. Le fetch sélectif GitHub devient donc la voie
> PRINCIPALE de P7, pas une extension optionnelle « P7-bis ».
>
> **Décision utilisateur (2026-07-18)** : héberger son PROPRE pack curaté (alt-arts,
> traductions rassemblées) sur un dépôt GitHub **privé**, pas public — les images
> concernées (art de cartes, IP One Piece/Bandai) ne lui appartiennent pas ; un dépôt
> public serait de la redistribution publique de contenu protégé (risque DMCA constaté sur
> des projets communautaires similaires d'autres TCG), alors qu'un dépôt privé est bien
> plus proche d'une sauvegarde personnelle. Conséquence technique : P7 doit supporter
> l'authentification GitHub (token), pas seulement les dépôts publics anonymes.

1. **`studio/assets/sourcefetch.py`** (nouveau) — abstraction « explorer puis récupérer » :
   - `list_remote_files(source_url, token=None) -> list[{path, size}]` : implémenté pour
     GitHub (Tree API) ; pour tout le reste (Dropbox, zip direct, dossier local), renvoie
     `None` (signale « pas d'exploration à distance possible » — pas une erreur, un mode
     dégradé).
   - `fetch_selected(source_url, paths, token=None) -> dossier local` : télécharge
     UNIQUEMENT les chemins demandés, en réutilisant `nettls.ssl_context()` déjà en place.

   **Dépôts privés (token)** : `token` = un Personal Access Token GitHub (scope `repo`,
   généré par l'utilisateur dans ses paramètres GitHub — jamais demandé/stocké par un tiers).
   - Tree API : `Authorization: Bearer <token>` sur `api.github.com/repos/.../git/trees/...`
     — fonctionne pour un dépôt privé exactement comme pour un public, juste avec l'en-tête
     en plus (documenté, sans ambiguïté).
   - Contenu par fichier : l'API Contents (`api.github.com/repos/.../contents/<chemin>`,
     même en-tête `Authorization`) renvoie le contenu en base64 — **chemin confirmé** par
     la doc GitHub pour les dépôts privés. `raw.githubusercontent.com` avec un token en
     en-tête fonctionne aussi en pratique sur du public/privé selon la doc GitHub, mais
     **à vérifier empiriquement** une fois qu'on implémente avec un vrai dépôt privé et un
     vrai token (ne pas se fier uniquement à la doc pour ce point précis) ; l'API Contents
     reste le repli sûr si jamais `raw.` se comporte différemment que prévu pour le privé.
   - **Stockage du token** : fichier de config local (`~/.optcgsim-studio/config.json` ou
     variable d'environnement `OPTCG_STUDIO_GITHUB_TOKEN`) — jamais commité, jamais loggé,
     jamais inclus dans un rapport de job ni un message d'erreur (traiter comme un secret,
     au même titre qu'un mot de passe : redaction systématique si un token apparaît dans un
     message d'exception avant de le faire remonter à l'UI/aux logs).
2. **Intégration à `add_pack()`** : si `list_remote_files` renvoie une liste ET qu'un
   filtre (`only_cards`/`only_categories`) est fourni → ne télécharger QUE les fichiers
   dont le nom matche le filtre (classification par NOM DE FICHIER, sans avoir à
   télécharger pour classifier — le nommage `<ID>[_OVERRIDE][_small].<ext>` suffit à
   filtrer AVANT de fetcher). Sinon (Dropbox, pas de filtre) → comportement actuel
   inchangé (zip complet, filtré à la normalisation — économie disque seulement, comme
   dans la version précédente de ce plan).
3. **`only_categories`** : cards/playmats/cardbacks/backgrounds/translation — au moment du
   FETCH (GitHub) ou de la normalisation (autres sources).
4. **`only_cards`** : restreint aux ids d'un ensemble donné, calculé depuis :
   a. **un ou plusieurs decks déjà en base** (« importer seulement pour ce(s) deck(s) ») —
      réutilise EXACTEMENT la logique déjà écrite pour `coverage()`
      (`set(deck["cards"]) | {deck["leader"]}`, union sur les decks cochés) ;
   b. **« leaders alternatifs uniquement »** — nécessite de savoir QUELS ids sont des
      leaders. Trouvé : le projet frère `optcgsim-haki` a déjà cette base
      (`data/card_stats.json`, 2558 cartes, champ `card_type == "Leader"`). À vendoriser
      (même principe que le parser vendorisé dans rogue-lab) dans un petit module
      `studio/assets/cardmeta.py` exposant `is_leader(card_id) -> bool`.
5. **UI** : quand `list_remote_files` réussit (source GitHub), afficher un aperçu AVANT
   téléchargement (« 2942 fichiers, 2,2 Go — filtrer avant de télécharger ? ») avec
   sélecteur deck(s)/leaders-only/catégories, et l'estimation de taille APRÈS filtre
   (calculable puisque les tailles exactes sont connues sans rien télécharger). Pour les
   sources sans exploration distante (Dropbox…), le filtre reste disponible mais agit
   après téléchargement complet (disque seulement) — message honnête dans l'UI :
   « cette source ne permet pas de filtrer avant téléchargement ».
6. **Rapport** : nouvelle catégorie « hors périmètre (filtré par choix) », distincte de
   « non classé » — un exclu volontaire n'est pas une erreur de reconnaissance.
7. **Configuration du token** : `studio config set-github-token <token>` (CLI, écrit dans
   le fichier de config local) + champ dédié dans l'UI (type `password`, jamais affiché en
   clair une fois saisi, jamais renvoyé tel quel par l'API — un `GET` de configuration ne
   renvoie qu'un booléen « configuré : oui/non », jamais la valeur). Sans token configuré,
   `list_remote_files`/`fetch_selected` fonctionnent quand même pour un dépôt PUBLIC
   (comme mesuré) ; un dépôt privé sans token renvoie une erreur claire (« dépôt privé ou
   introuvable — configure un token GitHub ») plutôt qu'un 404 opaque.

Effort : ~1-1,5 session (le fetch sélectif GitHub ajoute plus de substance que prévu par
rapport à la V1 « disque uniquement » du plan ; le support du token est un ajout mineur au
même chantier, pas une charge supplémentaire significative).

## Chantier P8 — Dépôt(s) privé(s) d'images + import granulaire par type de carte

Deux demandes liées (2026-07-19) : (a) comment organiser le/les dépôt(s) GitHub privé(s)
d'images custom ; (b) permettre l'import par petite partie (leaders, événements, dons, tapis…).

### Constat de départ (ce qui existe déjà)

- **P7 importe déjà sélectivement depuis UN dépôt GitHub** (Tree API + filtres
  `only_categories`/`only_cards`, avec token privé). L'import « par parties » est donc une
  question de FILTRE, pas d'obligation de découper en plusieurs dépôts.
- Filtres actuels : catégories (cards/playmats/cardbacks/backgrounds/translation) + ids de
  cartes (via decks ou leaders). Manque : filtrer par TYPE de carte (événement, stage,
  personnage) et traiter les DON comme une catégorie à part.

### (a) Organisation des dépôts — recommandation

- **Layout = miroir StreamingAssets** (déjà attendu par packlib/P7, zéro transformation) :
  `Cards/<SET>/<ID>.png` (+ `<ID>_small.jpg`), `Playmats/`, `CardBacks/`,
  `Cards/Don/Don.png`, `TRANSLATION.txt`. À DOCUMENTER (voir P9 pour le pendant « spec »).
- **Un seul dépôt suffit techniquement** (P7 filtre à la volée) ET c'est le plus ergonomique.
  → **Découper en plusieurs dépôts UNIQUEMENT si la taille l'impose** : GitHub recommande des
  dépôts < 1 Go (limite dure ~5 Go, fichiers < 100 Mo). Une collection d'alt-arts complète
  peut approcher 2 Go (cf. repo FR = 2,2 Go) → dans ce cas, **découper par TYPE DE CONTENU**
  (alt-arts / traductions / playmats-cardbacks), pas par set : ça épouse les catégories du
  studio et les cadences de mise à jour distinctes. Le studio gère déjà N sources (chaque
  dépôt ajouté est suivi/mis à jour indépendamment via P3).
- Décision ouverte : un dépôt « tout-en-un » (simple) OU trois dépôts par type de contenu
  (scalable) — dépend du volume réel que l'utilisateur compte héberger. Recommandation :
  commencer mono-dépôt, scinder seulement si on dépasse ~1 Go.

### (b) Import granulaire par type — implémentation

1. **`cardmeta` étendu** : vendoriser une table id→type complète (`card_types.json`, 43 Ko,
   2558 cartes ; via `scripts/refresh_leaders.py` renommé/élargi). Expose `card_type(id)` et
   `ids_of_type("Event"|"Stage"|"Character"|"Leader")`. Remplace/complète `leaders.json`.
2. **Filtre `only_types`** : généralise `leaders_only` — `only_types={"Event"}` → `only_cards`
   = tous les ids de ce type (même mécanisme que leaders, qui devient `only_types={"Leader"}`).
3. **DON** : `Cards/Don/Don.png` traité comme une (pseudo-)catégorie `don` dans `keep_rel`
   (aujourd'hui il tombe dans « cards » sans id — le rendre filtrable à part).
4. **Surface** : CLI `--only-type leader,event,don` ; UI cases à cocher par type ; aperçu de
   taille par type (comme l'aperçu « leaders » déjà en place — juste étendu aux autres types).
5. **Tests** : filtres par type sur arbo synthétique + fetch sélectif mocké (comme P7-d).

Effort : ~½ session (extension du socle P7 déjà en place ; le gros est fait).

### (c) Constructeur de dépôts depuis liens partagés — FAIT (2026-07-19)

Demande de suivi : **générer le contenu des dépôts privés à partir des liens déjà partagés
(Dropbox / Drive / GitHub)**. Décisions utilisateur : Drive = **fichiers/zip partagés** (pas
de crawl de dossier, zéro auth) ; découpage **par famille + cartes par type** ; **génération
locale, l'utilisateur pousse** (pas de push automatique).

- `packlib` : téléchargement **Google Drive** (fichier/zip partagé) via `_drive_download`
  (pot à cookies + jeton `confirm` d'analyse antivirus) ; `ingest` route les liens Drive et
  gère le **fichier unique** (pas seulement les zip) via `_materialize`. Dropbox-dossier-zip
  et GitHub-codeload déjà en place.
- `studio/assets/repobuild.py` : `build(sources, out, cards_as, split_cards_by_type, git_init)`
  → un dépôt par **famille** (`cards-alt` / `translations` / `playmats` / `cardbacks-don`),
  cartes **sous-classées par type** (`Leaders/Cards/<SET>/<ID>.png` …, nom canonicalisé),
  layout compatible import (ancêtre `Cards/` reconnu par `classify_rel`). `MANIFEST.json` +
  `README` + `git init` par dépôt ; collisions (dernière source gagne) et non-classés
  rapportés ; alerte au-delà de ~900 Mo.
- CLI `studio repos build <sources…> --out DIR [--cards-as alt|translated] [--no-split]
  [--no-git]` ; affiche la recette `git remote add … && git push` (jamais de push auto).
- Tests : `tests/test_repobuild.py` (routage, build hors-ligne via ingest factice,
  collisions, git init, parsing/ingest Drive). 152 verts.
- Reste possible (non demandé) : surface UI, et crawl de **dossier** Drive via clé API.

### (d) Mise à jour à chaque sortie de set — FAIT (2026-07-19)

Suivi direct de (c) : « il faudrait être capable de mettre à jour facilement nos repos à
chaque sortie ». Deux manques identifiés dans `build()` initial : (1) le re-lancer sur un
`--out` déjà construit signalait CHAQUE fichier existant comme une « collision » (bug — la
détection comparait à l'existence sur disque, pas aux écritures de CE run) ; (2) il fallait
retaper toutes les sources à chaque fois.

- **Fix collision** : la détection ne compare plus qu'aux chemins déjà écrits PENDANT le même
  appel de `build()` — une collision ne désigne que deux sources du même run visant la même
  cible. Rejouer `build()` sur un `--out` existant est désormais sûr (idempotent).
- **`.repos-build.json`** (racine de `--out`, hors des dépôts de famille → jamais poussé) :
  chaque `build()` enregistre ses `sources`/`cards_as`/`split_cards_by_type` (dédupliqué par
  config, pas par sources — relancer avec des sources différentes mais la même config
  remplace l'entrée).
- **`repobuild.update(out_dir)`** / CLI `studio repos update --out DIR` : relit ce journal et
  rejoue chaque config SANS redemander les liens.
- **Diff par dépôt** (`RepoStat.added/changed/orphans`, sha1 contre le `MANIFEST.json`
  précédent) : ajoutés, modifiés, et orphelins (disparus de la source — **jamais supprimés
  automatiquement**, juste signalés) — sert de base au message de commit.
- Tests : rebuild sans fausse collision, diff ajouté/modifié/orphelin, journal dédupliqué,
  `update()` rejoue sans repasser les sources, erreur propre si `--out` jamais construit.
  159 verts.

### (e) Fix — DON!! alternatif mal classé (retour d'usage réel, 2026-07-19)

Remontée après un premier import réel depuis Dropbox : les alternatifs de DON!! atterrissaient
dans `cardbacks-don/` comme si c'était des dos de carte, alors que ce sont des **cartes**
(reskins d'un asset carte unique), même famille que les alt-arts. Erreur de modélisation
initiale (j'avais bundlé DON avec les vrais dos de carte dans une même famille par commodité).

- `route()` : la catégorie `don` va maintenant dans la famille **CARTES** (`cards-alt` /
  `translations` selon `--cards-as`), sous son propre sous-dossier de type `Don/` (parallèle à
  Leaders/Characters/Events/Stages) — `Don/Cards/Don/<fichier>`.
- Le dépôt `cardbacks-don` est **renommé `cardbacks`** : ne contient plus que les VRAIS dos de
  carte (`CardBacks/*.png`).
- Compatible import inchangé : `classify_rel` retrouve la catégorie `don` en cherchant
  l'ancêtre `Cards/` puis `Don/` dans le chemin, quel que soit le préfixe de type devant —
  vérifié par trace du code, pas juste supposé.
- **⚠️ Ne s'auto-corrige PAS sur un `--out` déjà généré avec l'ancien code** : comme le NOM de
  famille change (`cardbacks-don` -> `cardbacks`), le mécanisme de diff/orphelins ne voit rien
  à nettoyer (ce n'est pas un renommage de fichier dans la même famille, c'est un dossier que
  le nouveau code ne touche plus jamais). Qui a déjà lancé `repos build` avant ce fix doit
  **supprimer manuellement** l'ancien `cardbacks-don/` (ou le renommer et retirer les images
  DON!! qui y traînent) avant de relancer `repos build`/`repos update`.
- Tests : `route("don", …)` -> famille cartes ; build de bout en bout vérifie l'absence de
  `cardbacks-don/` et la présence de `cards-alt/Don/…`. 160 verts.

### (f) Fix — rebuild sur un vieux MANIFEST (2026-07-19)

Un `MANIFEST.json` généré AVANT (d) avait `"files"` = ENTIER (le compte) ; le nouveau code de
diff s'attend à une map chemin->sha1 et plantait (`TypeError: 'int' object is not iterable`)
en relançant `repos build` sur un `--out` déjà construit avec l'ancien format. Fixé : un
`"files"` non-dict est ignoré (diff reparti à vide, tout en « ajouté »), et réécrit au nouveau
schéma. +1 test de régression. 161 verts.

### (g) `--path-prefix` — variantes multiples dans une même source (2026-07-19)

Retour d'usage : une source (repo GitHub FR) peut mélanger « traductions, cartes classiques
et cartes alternatives ». Or `_canonical_card_name` retire les suffixes parasites (`_alt`,
`_OVERRIDE`…) pour produire UN nom canonique par id — deux variantes du même id traitées dans
le MÊME `build()` se disputent donc la même cible (collision, dernière source gagnante,
perte silencieuse si le rapport n'est pas lu).

- `build(..., path_prefix=...)` : ne traite que les fichiers sous ce sous-dossier de la
  source ; les autres sont comptés (`RepoBuildReport.excluded_by_prefix`), jamais silencieux.
- CLI `--path-prefix FR_classique`. Un `build()` par variante (préfixe + `cards_as` distincts,
  ex. `translated` / `translated-alt`) préserve les deux au lieu d'en perdre une.
- Journal `.repos-build.json` : `path_prefix` fait partie de la clé de dédup (deux préfixes
  sous le même `cards_as` restent deux entrées séparées, toutes deux rejouées par `update()`).
  Rétrocompatible : une entrée sans `path_prefix` se relit comme `None`.
- Tests : collision démontrée sans préfixe, séparation propre avec préfixes, journal + replay
  par `update()` des deux configurations. 164 verts.

**Fix immédiat (même jour)** : l'utilisateur a demandé si un `TRANSLATION.txt` HORS des deux
sous-dossiers (à la racine de la source) serait quand même pris en compte — la réponse était
NON avec la première implémentation (le filtre excluait tout ce qui n'était pas sous le
préfixe, y compris les assets partagés). Corrigé : `--path-prefix` ne restreint QUE les
catégories `cards`/`don` (les seules canonicalisées par id, donc à risque de collision) ;
traduction/playmats/dos de carte sont des assets partagés, toujours inclus quel que soit le
préfixe — pas besoin d'un 3ᵉ appel juste pour la traduction. +2 tests. 165 verts.

### (h) `--lang` — découpler la langue de traduction de la variante d'art (2026-07-19)

Suite directe : en donnant les vrais noms de dossiers (`FR_classique`, `FR_full_art`),
l'utilisateur a soulevé que `TRANSLATION.txt` pourrait un jour exister pour d'autres langues.
Vrai angle mort : la traduction suivait un alias FIXE (`translations`), indépendant de
`cards_as` — une langue future (ES) écraserait la FR, et deux variantes d'art de la MÊME
langue partageant l'alias `translated` se disputaient déjà le même fichier.

- `route(..., lang=...)` : la catégorie `translation` va vers `translations-<lang>` si `lang`
  est fourni (sinon alias fixe `translations`, inchangé) — un axe INDÉPENDANT de `cards_as`.
- `build(..., lang=...)` / CLI `--lang fr` : deux builds de variantes d'art différentes
  (classique, full-art) avec le MÊME `--lang fr` regroupent leur traduction dans **un seul**
  dépôt `translations-fr/`, tout en gardant leurs cartes dans des dépôts séparés
  (`--cards-as` distincts). Une langue future (`--lang es`) va dans `translations-es/`, sans
  jamais toucher au FR.
- Journal `.repos-build.json` : `lang` rejoint la clé de dédup (rétrocompatible, `None` si
  absent) ; `update()` le rejoue.
- Tests : routage `lang` indépendant de `cards_as`, deux variantes convergent vers un seul
  dépôt de langue, langues distinctes jamais confondues, journal + replay. 169 verts. Validé
  en CLI réel avec les noms exacts de l'utilisateur (`FR_classique`/`FR_full_art`).

### (i) Fix — `repos build` téléchargeait tout le dépôt même avec `--path-prefix` (2026-07-19)

Incident réel de l'utilisateur : `repos build` sur le vrai repo FR (`Sparklight-TL/
OPTCGSim_FR`, 2,2 Go, 2943 fichiers) avec `--path-prefix FR_classique` a planté à 14 % de
l'extraction : `zipfile.BadZipFile: Bad CRC-32`. Cause racine double :

1. **Gaspillage** : `path_prefix` ne filtrait qu'APRÈS extraction — le dépôt ENTIER était
   téléchargé et extrait, même en ne gardant qu'une fraction. Or `sourcefetch` (P7) sait déjà
   faire un fetch sélectif fichier-par-fichier sur GitHub (mesuré : 98 % d'économie) — jamais
   branché sur `repobuild`.
2. **Fragilité** : un zip monolithique de plusieurs Go expose une corruption réseau ponctuelle
   (CRC invalide sur un membre) qui fait échouer TOUTE l'extraction, sans retry ni message
   clair (trace Python brute).

Fix :
- `repobuild._ingest_scoped()` : quand `path_prefix` est actif ET la source est un dépôt
  GitHub explorable, liste les fichiers distants (`sourcefetch.list_remote_files`), calcule le
  sous-ensemble à garder avec la MÊME logique que le filtre post-extraction
  (`_in_prefix_scope`, factorisée), et ne télécharge QUE ceux-là
  (`sourcefetch.fetch_selected`). Repli sur `ingest()` complet si la source n'est pas
  explorable (Dropbox, Drive, zip/dossier local) ou si `path_prefix` n'est pas fourni (rien à
  économiser). `build(..., token=...)` : PAT GitHub pour dépôt privé, jamais persisté dans
  `.repos-build.json` (secret) — repassé explicitement à chaque `update()`. CLI : les deux
  commandes réutilisent automatiquement `Config().github_token()` (comme `packs add`).
- `packlib.ingest()` : retry automatique (3 tentatives, backoff) sur `BadZipFile`/`URLError`
  pour tout téléchargement zip (GitHub complet, Dropbox, Drive) — un échec après 3 tentatives
  lève un `PackError` avec un message clair plutôt qu'une trace Python.
- Tests : fetch sélectif déclenché avec prefix+GitHub (seuls les fichiers du périmètre sont
  TÉLÉCHARGÉS, pas juste filtrés après coup), token propagé, repli sur source non explorable,
  pas de fetch sélectif sans préfixe, retry réussi puis retry épuisé. 175 verts. Réseau réel
  vérifié (API Tree GitHub répond correctement depuis cet environnement).

### (j) Réparation CIBLÉE au lieu d'un nouveau téléchargement complet (2026-07-19)

Retour direct sur (i) : « récupérer le fichier temporaire du téléchargement avant extraction
au lieu de le retélécharger ». Le retry (i) redemandait l'archive ENTIÈRE à chaque tentative
— gaspillage évitable pour une corruption qui, en pratique, ne touche qu'UNE poignée de
membres sur plusieurs milliers (le CRC est vérifié par fichier).

- `_safe_extract` devient TOLÉRANT : n'arrête plus l'extraction au premier membre en CRC
  invalide (le zip-slip, lui, reste une erreur DURE — sécurité, jamais toléré). Elle collecte
  les noms des membres en défaut et continue, plutôt que de perdre tout le travail déjà fait
  sur les milliers d'autres membres sains.
- `_materialize` renvoie désormais `(dossier, membres_corrompus)`.
- `_repair_corrupted(out, corrompus, source_url, token, on_progress)` : patch CIBLÉ — ne
  re-télécharge QUE les quelques fichiers en défaut, via `sourcefetch.fetch_selected` (API
  Contents GitHub, fichier par fichier). Renvoie `False` (repli sur un nouveau téléchargement
  complet — seul recours restant) si la source n'est pas un dépôt GitHub explorable ou si le
  patch échoue à son tour.
- `ingest()` tente ce patch AVANT de retomber sur le retry complet (i) : sur le repo FR réel,
  une poignée de fichiers corrompus ne coûterait plus qu'une poignée de requêtes API Contents,
  pas un nouveau téléchargement de 2,2 Go.
- Tests : extraction tolérante démontrée par corruption BYTE-EXACTE d'un vrai zip (CRC
  falsifié précisément, pas juste simulé), patch réussi via sourcefetch mocké, repli propre
  quand la source n'est pas explorable — et surtout le test de bout en bout qui prouve
  l'économie : `_download` appelé **une seule fois**, seul le fichier en défaut est re-fetché,
  le reste de l'archive (des milliers de fichiers sains) n'est jamais retouché. 180 verts.

## Chantier P9 — Publier le format « pack de decks » (contribution communautaire)

Objectif : permettre à la communauté de créer et partager des packs de decks (le format
`deckpack.json` de P6 existe déjà et fonctionne — il s'agit de le PUBLIER et d'outiller les
contributeurs).

1. **`docs/SPEC-deckpack.md`** : spécification formelle du format — champs (`name`, `author`,
   `schema_version`, `decks[]` avec `name`/`tags`/`text`|`file`|`source_url`), règles de
   validation (1 leader + 50 cartes, ≤4/id…), exemples complets, et guide d'hébergement
   (déposer le `deckpack.json` sur un dépôt GitHub public, partager l'URL → les autres font
   `studio decks import-pack <url>`). C'est le « catalogue partageable » évoqué en P5, mais
   pour les decks et porté par la communauté.
2. **`schema_version`** : ajouter le champ au format (défaut 1) pour la compat ascendante ;
   `deckpack.resolve` avertit si version future inconnue plutôt que d'échouer en silence.
3. **`studio decks validate-pack <source>`** : résout SANS persister (le rapport
   imported/failed de P6 existe déjà) — permet à un contributeur de vérifier son pack avant
   publication. API `POST /api/deckpacks/validate` (dry-run) pour l'UI.
4. **(optionnel) JSON Schema** (`docs/deckpack.schema.json`) pour validation par des outils
   externes/éditeurs.
5. **Tests** : validate-pack (dry-run ne persiste rien), schema_version future = avertissement.

Décision ouverte : où « publie »-t-on la spec ? (README du repo studio public + un dépôt
d'exemple `deckpack-examples`). Pas de registre central géré par le studio (cohérent avec
l'abandon de P5) — la communauté héberge et partage ses propres URLs.

Effort : ~½ session (surtout de la doc + une commande validate ; le moteur existe).

## Garde-fous inchangés

Le chemin d'écriture unique reste `_swap` (whitelist, magic-bytes+dimensions, atomique,
backup pristine + manifeste, restore intégral, zéro élévation de privilèges). La packlib ne
fait qu'AMONT. Les téléchargements se font sur demande explicite de l'utilisateur (taille
annoncée), jamais automatiquement en arrière-plan hors `packs update`.

## Ordre et effort

| # | Chantier | Effort | État |
|---|---|---|---|
| P0 | apply_mirror | ~½ session | ✅ fait |
| P1 | packlib (3 layouts + rapport) | ~1 session | ✅ fait |
| P2 | CLI packs | ~½ session | ✅ fait |
| P3 | --follow / update / reapply | ~½ session | ✅ fait |
| P4 | frontend dropzone+préview+couverture | ~1-2 sessions | ✅ fait (+ jobs de fond) |
| P5 | catalogue communautaire | — | ❌ ABANDONNÉ (doublon P7 + P3-follow) |
| P6 | pack de decks (import groupé) | ~1 session | ✅ fait (deckpack.json) |
| P7 | import sélectif (fetch ciblé GitHub + filtre disque partout) | ~1-1,5 session | ✅ fait (a→e, token privé inclus) |
| P8 | dépôt(s) privé(s) + import granulaire par type de carte | ~½ session | ✅ fait (card_types, --only-type, DON, UI par type ; multi-dépôts documenté) |
| P9 | publier le format deckpack (spec + validate contributeur) | ~½ session | ✅ fait (repo optcgsim-deckpacks : spec+schema+exemple+validate CI ; studio: schema_version + validate-pack) |

Décisions ouvertes historiques (résolues) :
- `Custom Cards`/`Extra Alts` (Dropbox) : le rapport « non-classés » de P1 les révèle.
- P7 : fetch sélectif avant DL pour GitHub (98 % d'économie mesurée), filtre disque sinon.

Décisions ouvertes pour P8-P9 (à trancher avant d'implémenter) :
- P8 : TRANCHÉ (2026-07-19) -> PLUSIEURS dépôts par type de contenu dès le départ (les
  images sont toujours volumineuses ; on évite d'atteindre les limites GitHub). Convention à
  documenter : un dépôt par famille (alt-arts, traductions, playmats+cardbacks+dons).
- P9 : TRANCHÉ (2026-07-19) -> repo DÉDIÉ (optcgsim-deckpacks) portant spec + schema +
  exemple + README de contribution. Pas de registre central géré par le studio.
- Ordre suggéré : P8 (débloque l'import par type, extension directe de P7) puis P9 (doc +
  petite commande de validation).
