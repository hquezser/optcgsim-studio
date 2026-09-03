# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/).
Ce projet suit le [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

Durcissement avant première publication. Les correctifs de sécurité ci-dessous portent sur du
contenu **tiers** (packs `.zip`, `deckpack.json`, `collection.json` distants) : le studio est
fait pour ingérer des fichiers venant d'inconnus, et les traitait avec trop de confiance.

### Sécurité

- **Exécution de code dans l'interface via un nom de deck ou de pack.** Les noms venant d'un
  `deckpack.json` ou d'un dépôt communautaire étaient insérés tels quels dans la page. Un nom
  contenant du balisage s'exécutait dans l'origine de l'interface, qui a un accès complet à
  l'API locale — donc de quoi appliquer des packs au jeu ou remplacer le token GitHub. Toutes
  les valeurs tierces sont désormais échappées, et les gestionnaires d'évènements passent par
  la délégation : aucune donnée tierce ne traverse plus un analyseur JS.
- **Requêtes forgées depuis une autre page (CSRF) et *DNS rebinding*.** N'importe quel site
  ouvert dans le même navigateur pouvait piloter l'API locale. Les en-têtes `Host` et `Origin`
  sont maintenant vérifiés, avec un rejet en 403. Une requête sans `Origin` (curl, CLI, client
  natif) reste acceptée.
- **Lecture de fichier arbitraire via `deckpack.json`.** Le champ `file` n'était protégé que
  contre `..` : un chemin **absolu** passait le contrôle, et si le fichier visé était une
  decklist valide, il était intégralement importé. Le chemin est désormais résolu et contraint
  au dossier du pack — `..`, chemins absolus et liens symboliques sont tous refusés.
- **Effacement de l'état local via un nom de pack.** Un nom contenant `..` pouvait faire
  supprimer `~/.optcgsim-studio/` — donc les sauvegardes d'origine, le manifeste et la base.
  Les noms sont assainis avant toute suppression, y compris quand ils viennent du `label` d'un
  `collection.json` distant.
- **Bornes des requêtes.** L'envoi d'un pack est lu par morceaux et plafonné (2 Gio ; 8 Mio
  pour un corps JSON) : un `Content-Length` mensonger ne peut plus faire gonfler la mémoire du
  serveur.

### Corrigé

- **Le DON d'un pack d'alt-arts n'était jamais importé.** Pour défaire l'emballage des archives
  GitHub, l'import descendait dans l'unique sous-dossier d'une source — y compris quand ce
  dossier était `Cards/`, la racine miroir elle-même. Un pack ne contenant que `Cards/` (le cas
  courant d'un dépôt d'alt-arts) perdait donc sa racine : les cartes s'en tiraient par chance,
  leur identifiant se relisant dans le nom de fichier, mais `Cards/Don/Don.png` devenait
  `Don/Don.png` — ni chemin miroir, ni identifiant de carte, donc « non classé » et jamais
  copié dans la bibliothèque.
- **Filtrer sur la catégorie « don » à l'application ne rendait jamais rien.** L'import et
  l'application classaient les chemins avec deux fonctions différentes, qui avaient divergé :
  l'une connaissait la catégorie `don`, l'autre rangeait `Cards/Don/…` dans `cards`. Cocher
  « don » à l'import marchait, `--only don` à l'application non. Une seule fonction classe
  désormais, aux deux étapes.
- **Traces d'erreur à chaque client parti en cours de route.** Un onglet fermé ou une image
  abandonnée pendant un défilement faisait imprimer une trace Python complète par le serveur,
  noyant les vraies erreurs. Ces abandons sont le cours normal des choses et sont désormais
  traités comme tels.
- **Des decklists de tournoi légales étaient refusées à l'import.** La limite de 4 exemplaires
  était appliquée à toutes les cartes, alors que certaines la lèvent dans leur propre texte
  (« you may have any number of this card in your deck » : Pacifista, Biscuit Warrior, Prisoner
  of Impel Down). Tout un archétype de `deckpack` publié devenait inimportable. La limite n'est
  plus bloquante : une liste réellement jouée est présumée légale, et notre table des cartes
  n'est qu'un instantané — une carte d'un set plus récent en serait absente. Un dépassement sur
  une carte non connue comme illimitée est relevé en ⚠ non bloquant. C'est le total de 50
  cartes qui reste le garde-fou contre les imports tronqués.
- **La restauration s'arrêtait au premier échec.** `restore-all` abandonnait dès qu'une
  sauvegarde manquait, laissant le jeu à moitié restauré sans le dire. Elle poursuit
  maintenant, renvoie `{restored, failed}`, et la CLI sort en code 1 en listant les échecs.
- **Sauvegarde d'origine écrasée.** La protection « on ne sauvegarde qu'une fois » reposait
  sur le manifeste seul : sa perte faisait enregistrer le fichier *déjà modifié* comme
  original, rendant la restauration impossible. Elle repose désormais aussi sur le disque.
- **Un deck en échec faisait perdre les suivants** à l'import d'un `deckpack` : seuls les
  échecs d'analyse étaient isolés, pas ceux d'écriture. `decks import-pack` sort maintenant en
  code 1 si un deck a échoué, au lieu de renvoyer 0 avec des croix à l'écran.
- **Un enregistrement corrompu masquait toute la bibliothèque.** Une seule cellule JSON
  illisible faisait échouer la lecture entière : plus aucun deck affiché, alors qu'un seul
  était en cause. L'enregistrement fautif est désormais isolé et signalé.
- **Token GitHub ignoré au repli.** Un dépôt privé dont l'exploration échouait retombait sur un
  téléchargement anonyme, avec un message d'erreur opaque.
- **Fichiers temporaires jamais nettoyés.** Chaque pack envoyé depuis l'interface laissait son
  `.zip` complet dans le dossier temporaire, succès comme échec.
- **Écritures JSON non atomiques** dans le constructeur de dépôts : une interruption laissait
  l'état de build tronqué, et le build suivant repartait de zéro.
- **Traces Python brutes en ligne de commande** sur une coupure réseau ou un disque plein.
  Les erreurs sont désormais lisibles, avec l'indice de correctif macOS pour les certificats,
  et `Ctrl-C` sort proprement en code 130.
- **Un élément refusé interrompait toute l'application d'un pack.** Seules les cartes étaient
  protégées : un tapis absent de votre installation — cas banal d'un pack communautaire —
  faisait échouer l'opération après que les cartes avaient déjà été posées, sans dire
  laquelle. Chaque refus est maintenant listé avec sa raison, et le reste s'applique.
- **Un champ oublié dans une requête répondait « introuvable »**, envoyant chercher un
  problème de ressource là où il n'y avait qu'un champ manquant. Les codes d'erreur ne
  dépendent plus non plus du verbe HTTP employé.
- **Une variable d'environnement `OPTCG_STUDIO_GITHUB_TOKEN` vide masquait le token
  configuré**, faisant échouer les dépôts privés sur un message incompréhensible.
- **Une URL GitHub pointant sur un sous-dossier** — celle qu'on copie après avoir navigué
  dans un dépôt — n'était pas reconnue : le studio téléchargeait le dépôt entier au lieu du
  strict nécessaire.
- **Le dossier de travail de `repos build` n'était jamais supprimé** : chaque construction
  laissait derrière elle une copie complète de tout ce qui avait été téléchargé.
- **Presse-papiers bloqué** : le délai d'attente était posé mais son expiration jamais
  traitée, d'où une trace brute.
- **Un fichier en échec faisait perdre tout le fetch sélectif** (import GitHub filtré,
  `repos build --path-prefix`). Concerne les dépôts de cartes/traductions alternatives,
  jamais les thèmes (qui passent par un téléchargement complet). Un fichier temporairement
  indisponible sur un dépôt de milliers d'images est bénin — la carte garde son art
  d'origine — mais faisait auparavant échouer tout l'import. Les échecs sont désormais
  sautés et listés dans le rapport du pack ; `_repair_corrupted` garde volontairement son
  exigence stricte (une réparation à moitié n'est pas une réparation).
- **La logique de persistance d'un deck était écrite à trois endroits légèrement
  différents** (service API, import unitaire CLI, import de pack CLI) — dérive déjà
  commencée. Unifiée dans `studio/decks/persist.py`, importé par les deux surfaces.

### Performance

- **Chargement de la bibliothèque de packs : 458 ms → 0,2 ms.** `GET /api/packs` calculait un
  SHA-1 de chaque fichier remplacé (~1 Go sur une collection complète) pour en déduire un état
  aussitôt jeté, alors que l'interface n'affiche qu'un compte. Mesuré sur 300 fichiers de
  300 Kio, extrapolé à 3 480 fichiers. L'interface appelle cette route à chaque chargement et
  après chaque action.

### Ajouté

- **Sélecteur d'emplacements** (`studio assets slots` / onglet *Thèmes* de l'interface). Le jeu
  n'a qu'**une** case par emplacement — un seul `Cards/Don/Don.png`, un tapis par couleur — là
  où un dépôt d'alt-arts en propose des centaines. Comme un pack ne peut que re-skinner de
  l'existant, c'était le **nom de fichier** qui tranchait tout seul : sur un dépôt réel, l'image
  nommée `Don.png` était posée et les **142 autres DON écartés en silence** (« aucune cible
  correspondante dans le jeu »), sans aucun moyen de dire laquelle on voulait. On choisit
  désormais, vignette par vignette, quelle image occupe chaque emplacement (DON, tapis, dos de
  cartes, fonds). Un choix est prioritaire sur les packs : ré-appliquer un pack ne l'écrase pas,
  il est reposé derrière et l'interface le signale. Réversible emplacement par emplacement.
- **`studio doctor`** : diagnostic complet de l'installation, à joindre à un rapport de bug.
  Vérifie notamment que chaque sauvegarde d'origine est bien présente — sans quoi un fichier
  du jeu ne pourrait plus être restauré, ce qu'on découvrait jusqu'ici le jour où on en avait
  besoin. Lecture seule stricte.
- **Intégration continue** : suite de tests sur Python 3.10 → 3.13, plus macOS et Windows
  (Windows informatif tant que la cartographie des chemins n'y est pas confirmée). Vérifie
  aussi que le point d'entrée `studio` répond et qu'aucun fichier sensible n'est versionné.
- **Garde-fou « zéro dépendance »** exécutable : un test échoue si un `import` hors
  bibliothèque standard apparaît dans `studio/`. La convention était écrite, rien ne
  l'appliquait. `certifi` reste toléré comme dépendance facultative, en import paresseux.
- **`SECURITY.md`** : périmètre, modèle de menace, traitement des secrets, signalement.
- **Premiers tests du câblage de la ligne de commande** (`tests/test_cli.py`), jusque-là non
  couvert.
- **Test verrouillant l'invariant de prévisualisation** : `apply_mirror(dry_run=True)` n'écrit
  rien dans le jeu. Le comportement était correct mais aucun test ne le protégeait.
- **Fuzzing des analyseurs de contenu tiers** (decklists, pages web, noms de fichiers
  d'archive, manifestes de collection) : ils doivent échouer proprement, jamais en trace
  brute. 9 620 cas passés, zéro exception inattendue.
- **Garde-fous de compatibilité Python 3.10**, la version minimale annoncée mais que rien ne
  vérifiait — une syntaxe trop récente aurait cassé l'installation sans message utile.

### Connu, non corrigé

- La résolution de conflits de synchronisation peut, dans un cas précis, marquer « propre » une
  modification locale écrasée. Nécessite une décision de conception sur la sémantique voulue.
- Le support Windows n'est pas vérifié sur machine réelle (`gamepaths.py` le marque
  `verified=False`).
- Le classement de la catégorie `OPBounty` est peut-être erroné, mais se tromper casserait
  l'application de packs : à trancher avec la connaissance du jeu.
