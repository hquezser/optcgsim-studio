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

## Chantier P5 — Catalogue communautaire (parcourir sans coller d'URL)

Objectif : une page « Découvrir » listant des sources connues, un clic = `packs add` (le
flux job existant, inchangé).

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

1. **`studio/assets/sourcefetch.py`** (nouveau) — abstraction « explorer puis récupérer » :
   - `list_remote_files(source_url) -> list[{path, size}]` : implémenté pour GitHub
     (Tree API) ; pour tout le reste (Dropbox, zip direct, dossier local), renvoie `None`
     (signale « pas d'exploration à distance possible » — pas une erreur, un mode dégradé).
   - `fetch_selected(source_url, paths) -> dossier local` : télécharge UNIQUEMENT les
     chemins demandés (GitHub : un `urlopen` par fichier vers `raw.githubusercontent.com`,
     en réutilisant `nettls.ssl_context()` déjà en place).
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

Effort : ~1-1,5 session (le fetch sélectif GitHub ajoute plus de substance que prévu par
rapport à la V1 « disque uniquement » du plan, mais le gain le justifie largement).

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
| P5 | catalogue communautaire | ~½ session | à trancher (registre distant ou non) |
| P6 | pack de decks (import groupé) | ~1 session | à trancher (qui écrit les manifestes) |
| P7 | import sélectif (fetch ciblé GitHub + filtre disque partout) | ~1-1,5 session | vendoring card_stats.json requis |

Décisions ouvertes historiques (résolues par la pratique) :
- `Custom Cards`/`Extra Alts` (Dropbox) : conventions non vérifiées — le rapport
  « non-classés » de P1 les révèle sans risque à chaque `packs add` réel.

Décisions ouvertes pour P5-P7 (à trancher avec l'utilisateur avant d'implémenter) :
- P5 : catalogue purement local (édité par le studio) vs. permettre un registre distant
  optionnel que l'utilisateur pointe explicitement (risque supply-chain à peser).
- P6 : qui curate les manifestes `deckpack.json` au départ (toi manuellement, ou une
  convention à proposer aux mainteneurs de decklists communautaires) ?
- P7 : résolu — fetch sélectif AVANT téléchargement pour GitHub (disque + bande passante,
  98 % d'économie mesurée), filtre après téléchargement pour Dropbox/autres (disque
  seulement, limitation confirmée : leur endpoint de listing refuse les appels scriptés
  sans navigateur complet, 403 constaté).
- Ordre de traitement suggéré : P7 (le plus mécanique, débloque immédiatement la
  couverture-de-decks déjà en place) → P5 (petit, haute valeur perçue) → P6 (le plus gros).
