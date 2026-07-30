"""Tests du gestionnaire de tâches de fond (studio.api.jobs)."""

import threading
import time

from studio.api.jobs import JobManager


def test_job_completes_and_reports_result():
    mgr = JobManager()
    jid = mgr.start("add", lambda reporter: {"ok": True})
    for _ in range(50):
        if mgr.get(jid)["status"] != "running":
            break
        time.sleep(0.01)
    st = mgr.get(jid)
    assert st["status"] == "done"
    assert st["result"] == {"ok": True}
    assert st["finished_at"] is not None


def test_job_captures_exception_as_error():
    mgr = JobManager()
    def boom(reporter):
        raise ValueError("kaboom")
    jid = mgr.start("add", boom)
    for _ in range(50):
        if mgr.get(jid)["status"] != "running":
            break
        time.sleep(0.01)
    st = mgr.get(jid)
    assert st["status"] == "error"
    assert st["error"] == "kaboom"


def test_progress_reporter_updates_job_state():
    mgr = JobManager()
    def work(reporter):
        reporter("download", 10, 100)
        time.sleep(0.02)
        reporter("download", 100, 100)
        return {"done": True}
    jid = mgr.start("add", work)
    seen_phase = False
    for _ in range(100):
        st = mgr.get(jid)
        if st["phase"] == "download":
            seen_phase = True
        if st["status"] == "done":
            break
        time.sleep(0.01)
    assert seen_phase
    assert mgr.get(jid)["done"] == 100 and mgr.get(jid)["total"] == 100


def test_get_unknown_job_returns_none():
    assert JobManager().get("nope") is None


def test_survives_independent_of_caller_not_polling():
    """Le job PROGRESSE pendant que personne ne l'interroge — onglet fermé ou rechargé.

    L'ancienne version de ce test lançait un job qui se terminait en microsecondes : elle ne
    prouvait donc que « un job déjà fini est fini », pas la survie du thread. Une régression
    qui tuerait les threads à la fin de la requête HTTP serait passée au vert. Ici on observe
    l'état `running` PUIS `done`, sans interroger pendant la durée du travail.
    """
    mgr = JobManager()
    demarre = threading.Event()

    def travail_long(reporter):
        demarre.set()
        time.sleep(0.4)          # dure plus longtemps que le silence de l'appelant
        return {"survived": True}

    jid = mgr.start("add", travail_long)
    assert demarre.wait(timeout=5), "le job doit démarrer sans qu'on l'interroge"
    assert mgr.get(jid)["status"] == "running", "le travail doit être EN COURS ici"

    time.sleep(0.6)              # « personne ne regarde » pendant tout le reste du travail

    st = mgr.get(jid)
    assert st["status"] == "done" and st["result"] == {"survived": True}


# ------------------------------------------------- P16.9 : le registre ne grossit pas sans fin
def _termine(mgr, jid, timeout=5):
    fin = time.time() + timeout
    while time.time() < fin:
        if mgr.get(jid)["status"] != "running":
            return
        time.sleep(0.005)
    raise AssertionError("job toujours en cours")


def test_finished_jobs_are_capped(monkeypatch):
    """Chaque `result` porte un rapport de pack complet : sans purge, enchaîner des imports
    dans une même session fait grossir le registre indéfiniment."""
    from studio.api import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "_MAX_TERMINES", 5)
    mgr = JobManager()
    for i in range(20):
        _termine(mgr, mgr.start("add", lambda reporter, i=i: {"n": i}))
    mgr.start("add", lambda reporter: {"declencheur": True})   # la purge a lieu au start
    assert len(mgr._jobs) <= 6, f"registre non purgé : {len(mgr._jobs)} entrées"


def test_purge_never_drops_a_running_job(monkeypatch):
    """Garde-fou : une purge qui emporterait un job EN COURS ferait perdre son suivi à l'UI."""
    from studio.api import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "_MAX_TERMINES", 1)
    monkeypatch.setattr(jobs_mod, "_RETENTION_S", 0)      # purge maximalement agressive
    mgr = JobManager()
    bloque = threading.Event()
    en_cours = mgr.start("add", lambda reporter: bloque.wait(timeout=5) or {"ok": True})
    for _ in range(10):
        _termine(mgr, mgr.start("add", lambda reporter: {}))
    assert mgr.get(en_cours) is not None, "un job en cours ne doit jamais être purgé"
    assert mgr.get(en_cours)["status"] == "running"
    bloque.set()


def test_recent_finished_job_is_still_readable_after_reload():
    """L'UI mémorise les ids en localStorage : un job tout juste fini doit rester consultable
    après un rechargement de page, sinon l'utilisateur voit un 404 sur ce qu'il a sous les yeux."""
    mgr = JobManager()
    jid = mgr.start("add", lambda reporter: {"ok": True})
    _termine(mgr, jid)
    for _ in range(5):
        _termine(mgr, mgr.start("add", lambda reporter: {}))
    assert mgr.get(jid)["result"] == {"ok": True}
