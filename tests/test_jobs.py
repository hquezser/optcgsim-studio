"""Tests du gestionnaire de tâches de fond (studio.api.jobs)."""

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
    """Le job continue même si personne n'interroge son état pendant un moment — simule la
    fermeture/rechargement d'onglet côté client : le thread serveur n'est jamais affecté."""
    mgr = JobManager()
    jid = mgr.start("add", lambda reporter: {"survived": True})
    time.sleep(0.1)          # « personne ne regarde » pendant un moment
    st = mgr.get(jid)
    assert st["status"] == "done" and st["result"] == {"survived": True}
