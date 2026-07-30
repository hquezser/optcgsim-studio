# Sécurité

## Ce que ce projet fait, et ne fait pas

`optcgsim-studio` **modifie des fichiers du jeu OPTCGSim** — uniquement des **cosmétiques**
(images de cartes, tapis, dos, fonds, fichier de traduction). Il ne touche jamais au moteur
de jeu, n'intercepte aucun trafic réseau du jeu, et n'automatise rien en partie.

Tout ce qu'il écrit est **réversible** : `studio assets restore-all` remet l'installation dans
son état d'origine, à l'octet près, à partir de sauvegardes prises avant la toute première
modification.

Le serveur de l'interface web écoute sur **127.0.0.1 uniquement**. Il n'est jamais exposé au
réseau et n'a aucune authentification — parce qu'il ne doit jamais être joignable de
l'extérieur (voir « Contrôle d'origine » plus bas).

## Modèle de menace

La menace principale n'est pas un attaquant réseau : c'est le **contenu tiers**. Le studio est
fait pour ingérer des paquets d'assets et des listes de decks venant d'inconnus — un `.zip`
trouvé sur un serveur Discord, un dépôt GitHub communautaire, un `deckpack.json` partagé. Ce
contenu est traité comme **hostile par défaut**.

| Surface | Entrée non fiable | Garde-fou |
|---|---|---|
| Archives de packs | noms de fichiers d'un `.zip` | extraction refusant la traversée (`zip-slip`), noms de packs assainis avant toute écriture |
| Images | contenu de fichier | liste blanche d'extensions, contrôle des octets magiques **et** des dimensions — un script déguisé en `.png` est rejeté |
| `deckpack.json` | champ `file` | chemin résolu et contraint au dossier du pack : `..`, chemin absolu et lien symbolique sont tous refusés |
| Noms de packs / decks / libellés de collection | texte libre | échappés avant insertion dans l'interface ; aucune donnée tierce ne traverse un analyseur JS |
| API locale | requêtes du navigateur | contrôle d'origine : en-têtes `Host` et `Origin` vérifiés |
| Écriture dans le jeu | — | chemin d'écriture unique (`AssetManager._swap`) : liste blanche, écriture atomique, sauvegarde d'origine, manifeste, restauration intégrale |

### Contrôle d'origine

Une page web quelconque, ouverte dans le même navigateur, pouvait autrefois envoyer des
requêtes à `http://127.0.0.1:8770` et piloter le studio à l'insu de l'utilisateur (CSRF), et
un nom DNS pointant sur `127.0.0.1` pouvait contourner l'isolement d'origine du navigateur
(*DNS rebinding*). L'API rejette désormais en **403** toute requête dont l'en-tête `Host`
n'est pas `127.0.0.1`/`localhost`, ou dont l'en-tête `Origin`, s'il est présent, ne correspond
pas. Une requête **sans** `Origin` reste acceptée : c'est le cas de `curl`, de la CLI et d'un
futur client natif, qui ne sont pas soumis au modèle d'origine du navigateur.

### Bornes des requêtes

Les corps de requête sont plafonnés (2 Gio pour l'envoi d'un pack, 8 Mio pour un corps JSON)
et lus **par morceaux** : un `Content-Length` mensonger ne peut pas faire gonfler la mémoire
du serveur.

## Secrets

Le seul secret manipulé est un **token GitHub personnel** (facultatif, pour explorer un dépôt
privé). Il vit dans `~/.optcgsim-studio/config.json`, en dehors du dépôt, avec les permissions
`600`. L'API ne le renvoie **jamais en clair** : elle expose seulement un booléen indiquant
qu'un token est configuré. La CI vérifie qu'aucun fichier de configuration ou d'identifiants
n'est versionné.

Si tu penses avoir exposé ton token, révoque-le sur GitHub — le studio n'en garde aucune copie
ailleurs que dans ce fichier.

## Ce qui reste hors périmètre

- **Aucune élévation de privilèges.** Si un dossier n'est pas accessible en écriture, le studio
  l'explique et s'arrête. Il ne propose jamais `sudo`.
- **Signature d'application macOS.** Modifier `StreamingAssets` invalide la signature du bundle
  du jeu. Une application déjà autorisée continue de se lancer, mais c'est une conséquence
  assumée du hot-swap, pas un problème de sécurité du studio. `restore-all` rétablit l'état
  d'origine.
- **Le contenu des packs eux-mêmes.** Le studio vérifie qu'une image est bien une image ; il ne
  juge pas ce qu'elle représente ni d'où elle vient.

## Signaler une vulnérabilité

Ouvre une *security advisory* privée sur le dépôt GitHub
(`Security` → `Report a vulnerability`), ou contacte directement le mainteneur. Merci de ne pas
ouvrir d'issue publique pour un problème exploitable.

Décris de préférence : ce que tu as fait, ce que tu attendais, ce qui s'est produit, et si
possible un `deckpack.json` ou un `.zip` minimal reproduisant le problème.

## Versions suivies

Le projet n'a pas encore de version publiée ; seule la branche par défaut est maintenue.
