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

### Performance

- **Chargement de la bibliothèque de packs : 458 ms → 0,2 ms.** `GET /api/packs` calculait un
  SHA-1 de chaque fichier remplacé (~1 Go sur une collection complète) pour en déduire un état
  aussitôt jeté, alors que l'interface n'affiche qu'un compte. Mesuré sur 300 fichiers de
  300 Kio, extrapolé à 3 480 fichiers. L'interface appelle cette route à chaque chargement et
  après chaque action.

### Ajouté

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

### Connu, non corrigé

- `fetch_selected` abandonne tout le téléchargement si un seul fichier échoue. Le rendre
  tolérant change le contrat de retour (que vaut un pack partiel ?) : décision d'API reportée.
- La résolution de conflits de synchronisation peut, dans un cas précis, marquer « propre » une
  modification locale écrasée. Nécessite une décision de conception sur la sémantique voulue.
- Le support Windows n'est pas vérifié sur machine réelle (`gamepaths.py` le marque
  `verified=False`).
