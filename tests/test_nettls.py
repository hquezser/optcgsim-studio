"""Tests du helper SSL (studio.nettls) — le correctif du bug CERTIFICATE_VERIFY_FAILED."""

import ssl

from studio.nettls import ssl_context


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
