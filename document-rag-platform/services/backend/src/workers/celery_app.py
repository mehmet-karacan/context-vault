"""Celery application definition (Aşama 2.3).

This is the module the ``worker`` container boots via
``celery -A src.workers.celery_app worker -l info`` (see
``docker-compose.yml``). Before this file existed, that command failed with
``The module src.workers.celery_app was not found`` and the worker container
crash-looped (``Exited (2)``) — see AKTIF_GOREV.md Bölüm 3 ("Worker komutu
gerçek bir celery_app modülüne bağlanmamış olabilir").

Broker and result backend are both the same Redis instance already injected
into the ``backend``/``worker`` containers via ``REDIS_URL``
(``src.config.Settings.REDIS_URL``).

Task modules are wired in via ``include=`` rather than importing them
directly at the bottom of this file, specifically to avoid a circular import
with ``ingestion_tasks`` (which itself imports ``celery_app`` from here to
get the ``@celery_app.task`` decorator). ``include`` defers the import until
Celery's loader actually needs it (worker bootstrap / first task lookup),
by which point this module has finished initializing.
"""

from celery import Celery

from ..config import settings

celery_app = Celery(
    "context_vault",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["src.workers.ingestion_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Ingestion jobs do real network I/O (MinIO, embedding gateway) and can
    # legitimately take a while; don't let Celery silently drop results we
    # never asked to expire quickly.
    result_expires=None,
)

# --- Worker-safe delivery & time limits (Aşama 2.5) --------------------------
# These make ingestion jobs robust to a worker being killed/restarted mid-run:
#
# * task_acks_late: the message is only acknowledged AFTER the task succeeds.
#   If the worker dies (kill -9, OOM, restart) while a job is in-flight, Celery
#   redelivers the task to another worker instead of losing it forever. This is
#   the mechanism behind the AKTIF_GOREV.md Aşama 2 kabul kriteri
#   "Worker yeniden başlatılsa job verisi kaybolmaz".
# * task_reject_on_worker_lost: a task whose worker vanished mid-execution is
#   returned to the queue as rejected (requeued) rather than silently dropped.
# * task_acks_on_failure_or_timeout: with autoretry, the *final* failure after
#   max_retries is exhausted is acknowledged so the broker does not loop the
#   message forever. Retries themselves happen inside the handled task, and the
#   job's on-disk state machine (idempotent wipe+rewrite) stays convergence-safe
#   across redeliveries either way.
# * worker_prefetch_multiplier=1: a slow ingestion task reserves at most one
#   message, so one long job cannot hoard every queued ingestion while the
#   rest of the workers sit idle.
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=settings.INGESTION_TASK_SOFT_TIME_LIMIT_SECONDS,
    task_time_limit=settings.INGESTION_TASK_TIME_LIMIT_SECONDS,
)

# Alias some Celery app-discovery conventions look for when given a bare
# module path (``-A src.workers.celery_app`` with no ``:attr`` suffix).
# Harmless to keep alongside ``celery_app`` — same object either name.
app = celery_app
