# PLAN — Import ergonomique de packs communautaires (cartes + customs)

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

## Garde-fous inchangés

Le chemin d'écriture unique reste `_swap` (whitelist, magic-bytes+dimensions, atomique,
backup pristine + manifeste, restore intégral, zéro élévation de privilèges). La packlib ne
fait qu'AMONT. Les téléchargements se font sur demande explicite de l'utilisateur (taille
annoncée), jamais automatiquement en arrière-plan hors `packs update`.

## Ordre et effort

| # | Chantier | Effort | Débloque |
|---|---|---|---|
| P0 | apply_mirror | ~½ session | Themer complet (menus+Don inclus) |
| P1 | packlib (3 layouts + rapport) | ~1 session | Dropbox Jon + repo FR |
| P2 | CLI packs | ~½ session | utilisable au quotidien |
| P3 | --follow / update / reapply | ~½ session | traduction FR toujours fraîche |
| P4 | frontend dropzone+préview+couverture | ~1-2 sessions | ergonomie grand public |

Décisions ouvertes :
- P4 après P2 (CLI d'abord, mon avis : oui — valide les rapports avant de les habiller) ?
- `Custom Cards`/`Extra Alts` (Dropbox) : conventions non vérifiées — le rapport
  « non-classés » de P1 les révélera sans risque au premier `packs add` réel.
