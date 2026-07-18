"""Tests du helper SSL (studio.nettls) — le correctif du bug CERTIFICATE_VERIFY_FAILED."""

import ssl
import urllib.error

from studio.nettls import is_cert_error, ssl_context


def test_ssl_context_returns_valid_context():
    ctx = ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED     # vérification jamais désactivée


def test_ssl_context_never_disables_verification_even_without_certifi(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_certifi(name, *a, **kw):
        if name == "certifi":
            raise ImportError("simulate absent")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_certifi)
    ctx = ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED     # repli silencieux, pas de désactivation


# ------------------------------------------------------------------ is_cert_error
# Régression : urlopen() enveloppe TOUJOURS l'échec de handshake SSL dans un URLError
# (jamais un ssl.SSLCertVerificationError nu qui remonterait tel quel) — un
# `except ssl.SSLCertVerificationError` direct ne l'attrape donc jamais. Constaté en
# conditions réelles : le message d'aide (CERT_FIX_HINT) n'était jamais affiché, seule
# l'erreur OpenSSL brute remontait via le job/l'exception.
def test_is_cert_error_detects_wrapped_url_error():
    reason = ssl.SSLCertVerificationError(
        "CERTIFICATE_VERIFY_FAILED", "self-signed certificate in certificate chain")
    exc = urllib.error.URLError(reason)
    assert is_cert_error(exc)


def test_is_cert_error_detects_raw_ssl_error():
    exc = ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED", "boom")
    assert is_cert_error(exc)


def test_is_cert_error_rejects_unrelated_url_errors():
    assert not is_cert_error(urllib.error.URLError("Name or service not known"))   # DNS
    assert not is_cert_error(urllib.error.URLError(ConnectionRefusedError()))
    assert not is_cert_error(ValueError("sans rapport"))
