"""Tâches de fond pour les opérations longues (téléchargement, normalisation, application).

Pourquoi : une requête HTTP synchrone qui bloque plusieurs minutes (un pack Dropbox peut
peser 600+ Mo) est fragile — l'utilisateur, ne voyant rien bouger, peut fermer l'onglet ou
recharger la page, ce qui coupe la requête. Un job tourne dans un THREAD SERVEUR indépendant
de la connexion HTTP : fermer l'onglet n'interrompt RIEN, l'opération va à son terme ; l'UI
n'a qu'à revenir consulter `GET /api/jobs/<id>` pour retrouver son état (y compris après un
rechargement de page, via l'id mémorisé côté client).

Un seul run de studio ui = un seul process = un registre de jobs en mémoire (perdu au
redémarrage). Suffisant : les jobs eux-mêmes ont déjà terminé leur travail sur disque/en base
avant que le job ne soit interrogé une dernière fois ; seul l'historique de suivi se perd.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class JobStatus:
    id: str
    kind: str                          # "add" | "upload" | "apply" (libre, pour l'affichage)
    status: str = "running"            # running | done | error
    phase: str = ""                    # "download" | "extract" | "normalize" | "apply" | ...
    done: int = 0
    total: int = 0                     # 0 = inconnu (l'UI affiche un spinner, pas une barre)
    result: dict | None = None
    error: str | None = None
    started_at: float = 0.0
    finished_at: float | None = None

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        return d


class ProgressReporter:
    """Callback passé aux fonctions longues : `reporter(phase, done, total)`."""

    def __init__(self, job: JobStatus, lock: threading.Lock):
        self._job = job
        self._lock = lock

    def __call__(self, phase: str, done: int = 0, total: int = 0) -> None:
        with self._lock:
            self._job.phase = phase
            self._job.done = done
            self._job.total = total


# Rétention des jobs TERMINÉS. L'UI mémorise les ids en localStorage et peut revenir les
# consulter après un rechargement de page : purger trop tôt donnerait un 404 sur un job que
# l'utilisateur voit encore affiché. Une heure couvre largement ce cas, et le plafond en
# nombre borne la mémoire quand on enchaîne beaucoup d'imports dans une même session (chaque
# `result` porte un rapport de pack complet).
_RETENTION_S = 3600
_MAX_TERMINES = 100


class JobManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, JobStatus] = {}

    def _purger(self) -> None:
        """Retire les jobs terminés trop vieux, ou en surnombre. Appelé sous verrou."""
        termines = [j for j in self._jobs.values() if j.finished_at is not None]
        limite = time.time() - _RETENTION_S
        a_jeter = {j.id for j in termines if j.finished_at < limite}
        restants = sorted((j for j in termines if j.id not in a_jeter),
                          key=lambda j: j.finished_at)
        if len(restants) > _MAX_TERMINES:
            a_jeter |= {j.id for j in restants[:len(restants) - _MAX_TERMINES]}
        for jid in a_jeter:
            del self._jobs[jid]

    def start(self, kind: str, fn: Callable[[ProgressReporter], dict]) -> str:
        """Lance `fn(reporter)` dans un thread démon. `fn` doit renvoyer le résultat (dict)
        ou lever une exception (capturée et stockée comme erreur du job)."""
        job = JobStatus(id=uuid.uuid4().hex, kind=kind, started_at=time.time())
        with self._lock:
            self._purger()          # jamais les jobs EN COURS, seulement les terminés
            self._jobs[job.id] = job
        reporter = ProgressReporter(job, self._lock)

        def run():
            try:
                result = fn(reporter)
                with self._lock:
                    job.status = "done"
                    job.result = result
                    job.finished_at = time.time()
            except Exception as e:  # noqa: BLE001 — surfacé au client via /api/jobs/<id>
                with self._lock:
                    job.status = "error"
                    job.error = str(e)
                    job.finished_at = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job.id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None
