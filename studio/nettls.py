"""Contexte SSL robuste pour les appels réseau du studio (zéro dépendance obligatoire).

Bug réel rencontré (macOS, Python.org) : l'installeur officiel de python.org ne peuple pas
le trousseau de certificats racine de Python (`Install Certificates.command` jamais exécuté)
-> `urllib.request.urlopen` échoue sur TOUT hôte HTTPS avec `CERTIFICATE_VERIFY_FAILED
self-signed certificate in certificate chain`, alors que `curl`/le navigateur fonctionnent
(ils utilisent le trousseau système). Aucun rapport avec Dropbox/GitHub eux-mêmes.

Fix : si le paquet `certifi` est disponible (fréquent — installé comme dépendance transitive
par beaucoup d'outils), on l'utilise comme trousseau de secours — un jeu d'autorités de
certification publiques légitime, identique à celui utilisé par `pip`/`requests`. Ce n'est PAS
une désactivation de la vérification : le contexte par défaut est tenté en premier ; `certifi`
n'intervient qu'en repli si le trousseau du système est cassé.
"""

from __future__ import annotations

import ssl
import urllib.error


def ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    try:
        ctx.load_verify_locations(_probe())
    except (ImportError, ssl.SSLError, OSError, FileNotFoundError):
        pass  # certifi absent, ou trousseau système déjà fonctionnel : rien à ajouter
    return ctx


def _probe() -> str:
    """Chemin du paquet certifi si dispo. Lève ImportError si absent (repli silencieux)."""
    import certifi
    return certifi.where()


def is_cert_error(exc: BaseException) -> bool:
    """Détecte une erreur de vérification de certificat — y compris enveloppée dans un
    `urllib.error.URLError` (le cas RÉEL en pratique : `urlopen()` enveloppe systématiquement
    les échecs de handshake SSL dans un URLError dont `.reason` porte l'exception SSL
    d'origine ; un `except ssl.SSLCertVerificationError` nu ne l'attrape donc JAMAIS — bug
    constaté : le message d'aide n'était jamais affiché, seule l'erreur OpenSSL brute
    remontait à l'utilisateur)."""
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)


CERT_FIX_HINT = (
    "Échec de vérification de certificat SSL. Cause fréquente sur macOS avec un Python "
    "installé depuis python.org : le trousseau de certificats racine n'a jamais été "
    "installé. Corriger avec l'une de ces options :\n"
    "  1. Lancer « Install Certificates.command » (dans /Applications/Python 3.x/)\n"
    "  2. pip install certifi   (le studio l'utilisera automatiquement)\n"
    "  3. Utiliser un Python installé via Homebrew (brew install python)"
)
