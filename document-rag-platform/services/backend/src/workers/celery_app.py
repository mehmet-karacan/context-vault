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

# Alias some Celery app-discovery conventions look for when given a bare
# module path (``-A src.workers.celery_app`` with no ``:attr`` suffix).
# Harmless to keep alongside ``celery_app`` — same object either name.
app = celery_app
