"""Aşama 2.3: unit tests for the ingestion Celery task's state machine.

These unit tests deliberately do not touch PostgreSQL, MinIO, or an LLM
gateway. Real schema behavior is covered by the integration suite. Here,
``db`` is a minimal in-memory fake that mimics just the SQLAlchemy
``Session`` surface ``run_ingestion_job`` actually calls (``get``, ``query``
+ ``filter``/``first``/``delete``/``order_by``, ``add``, ``commit``,
``flush``, ``rollback``), and ``storage``/``extract_text_fn``/
``chunk_text_fn``/``embed_texts_fn`` are fakes/stubs injected directly into
``run_ingestion_job`` — the pure(ish) core of the Celery task, factored out
specifically so it's testable this way (see
``src/workers/ingestion_tasks.py``).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from src.models import (
    Chunk,
    ChunkEmbedding,
    Document,
    DocumentArtifact,
    DocumentVersion,
    EmbeddingProfile,
    IngestionEvent,
    IngestionJob,
)
from src.workers import ingestion_tasks
from src.workers.ingestion_tasks import (
    IngestionJobError,
    RetryableIngestionError,
    StageTransitionError,
    run_ingestion_job,
)
from src.infrastructure.observability import (
    continue_trace,
    current_traceparent,
    metrics,
)


# --- Fake SQLAlchemy session -------------------------------------------------


def _matches(obj, criteria) -> bool:
    """Evaluates simple ``Model.column == value`` / ``.is_(value)`` filter
    criteria against an in-memory object. Falls back to "matches" (True) for
    any expression shape it doesn't understand, which is fine here because
    every test fixture is small enough that filters are only ever used to
    disambiguate between a couple of objects of the same type.
    """
    for crit in criteria:
        try:
            key = crit.left.key
            value = crit.right.value
        except AttributeError:
            continue
        if getattr(obj, key, None) != value:
            return False
    return True


class FakeQuery:
    def __init__(self, session, model):
        self.session = session
        self.model = model
        self._criteria = []

    def filter(self, *criteria):
        self._criteria.extend(criteria)
        return self

    def _matching(self):
        return [
            o
            for o in self.session.objects.get(self.model, [])
            if _matches(o, self._criteria)
        ]

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        matching = self._matching()
        return matching[0] if matching else None

    def all(self):
        return list(self._matching())

    def delete(self, synchronize_session=None):
        matching = self._matching()
        bucket = self.session.objects.get(self.model, [])
        for obj in matching:
            bucket.remove(obj)
        return len(matching)


class FakeSession:
    def __init__(self):
        self.objects: dict = {}

    def add(self, obj):
        self.objects.setdefault(type(obj), []).append(obj)

    def get(self, model, id_):
        if id_ is None:
            return None
        for obj in self.objects.get(model, []):
            oid = getattr(obj, "id", None)
            if oid is not None and str(oid) == str(id_):
                return obj
        return None

    def query(self, model):
        return FakeQuery(self, model)

    def commit(self):
        pass

    def flush(self):
        pass

    def rollback(self):
        pass


class FakeStorage:
    def __init__(self):
        self.data: dict = {}

    def put(self, key, data, content_type=None):
        self.data[key] = data
        return key

    def get(self, key):
        return self.data[key]

    def delete(self, key):
        self.data.pop(key, None)

    def exists(self, key):
        return key in self.data


# --- Fixtures ----------------------------------------------------------------


def _make_chain(db: FakeSession, storage: FakeStorage, *, active_version_id=None):
    """Seeds a Document + DocumentVersion + queued IngestionJob + the
    original file bytes already sitting in (fake) MinIO — the contract
    ``run_ingestion_job`` assumes an upstream caller has already set up.
    """
    document = Document(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        name="report.txt",
        size=123,
        status="uploaded",
        uploaded_at=datetime.now(timezone.utc),
        active_version_id=active_version_id,
    )
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        version_no=1,
        status="pending",
        storage_key=f"projects/{document.project_id}/documents/{document.id}/versions/x/original/report.txt",
        created_at=datetime.now(timezone.utc),
    )
    job = IngestionJob(
        id=uuid.uuid4(),
        version_id=version.id,
        status="queued",
        attempt=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(document)
    db.add(version)
    db.add(job)
    storage.data[version.storage_key] = b"hello world original bytes"
    return document, version, job


def _stub_chunker(chunks):
    return lambda text, chunk_size=None, overlap=None: list(chunks)


def _stub_embedder(dimension=1024):
    def _embed(texts, instruction=""):
        return [[0.1] * dimension for _ in texts]

    return _embed


def _stub_extractor(text="hello world extracted text"):
    return lambda file_path, filename: text


def test_celery_entry_binds_trace_before_setup_and_restores_ambient(monkeypatch):
    parent = f"00-{'1' * 32}-{'2' * 16}-00"
    ambient = f"00-{'a' * 32}-{'b' * 16}-01"
    seen: list[str | None] = []

    class EntrySession:
        def close(self):
            seen.append(current_traceparent())

    class EntryOrchestrator:
        def __init__(self, _db, _storage):
            seen.append(current_traceparent())

        def process_job(self, job_id, **kwargs):
            seen.append(current_traceparent())
            return {"job_id": job_id, "key": kwargs["inbox_idempotency_key"]}

    from src.application import ingestion_orchestrator as orchestrator_module

    monkeypatch.setattr(ingestion_tasks, "SessionLocal", EntrySession)
    monkeypatch.setattr(ingestion_tasks, "_build_storage", lambda: object())
    monkeypatch.setattr(orchestrator_module, "IngestionOrchestrator", EntryOrchestrator)

    with continue_trace(ambient):
        result = ingestion_tasks.process_ingestion_job.run("job-1", "key-1", parent)
        assert current_traceparent() == ambient

    assert result == {"job_id": "job-1", "key": "key-1"}
    assert seen == [parent, parent, parent]


def test_celery_entry_binds_and_resets_trace_when_session_setup_fails(monkeypatch):
    parent = f"00-{'1' * 32}-{'2' * 16}-01"
    ambient = f"00-{'a' * 32}-{'b' * 16}-01"

    def fail_session():
        assert current_traceparent() == parent
        raise RuntimeError("synthetic setup failure")

    monkeypatch.setattr(ingestion_tasks, "SessionLocal", fail_session)
    with continue_trace(ambient):
        with pytest.raises(RuntimeError, match="synthetic setup failure"):
            ingestion_tasks.process_ingestion_job.run("job-1", "key-1", parent)
        assert current_traceparent() == ambient


def test_celery_publish_stamp_contains_only_bounded_epoch(monkeypatch):
    from src.workers import celery_app as celery_module

    monkeypatch.setattr(celery_module.time, "time", lambda: 1_788_649_200.9)
    headers = {"task": "ingestion.process_ingestion_job", "argsrepr": "private"}
    celery_module._stamp_queue_age(headers=headers)
    assert headers["cv_enqueued_at_epoch"] == 1_788_649_200
    assert set(headers) == {
        "task",
        "argsrepr",
        "cv_enqueued_at_epoch",
    }


def test_job_failure_counter_records_only_terminal_celery_signal():
    from src.workers import celery_app as celery_module

    sender = type("Sender", (), {"name": "ingestion.process_ingestion_job"})()
    before = metrics.snapshot()["counters"].get("job.failures", 0)
    celery_module._record_ingestion_retry(sender=sender)
    assert metrics.snapshot()["counters"].get("job.failures", 0) == before
    celery_module._record_terminal_ingestion_failure(
        sender=type("Other", (), {"name": "other.task"})()
    )
    assert metrics.snapshot()["counters"].get("job.failures", 0) == before
    celery_module._record_terminal_ingestion_failure(
        sender=sender,
        exception=ingestion_tasks.JobCancelled("cancelled"),
    )
    assert metrics.snapshot()["counters"].get("job.failures", 0) == before
    celery_module._record_terminal_ingestion_failure(sender=sender)
    assert metrics.snapshot()["counters"]["job.failures"] == before + 1


def test_operational_observer_records_current_inventory_without_raw_keys(monkeypatch):
    class CountQuery:
        def filter(self, *_criteria):
            return self

        def scalar(self):
            return 2

    class ObserverSession:
        def query(self, _model):
            return CountQuery()

        def close(self):
            pass

    monkeypatch.setattr(ingestion_tasks, "SessionLocal", ObserverSession)
    result = ingestion_tasks.observe_operational_metrics.run()
    assert result == {
        "stale_leases": 2,
        "receipt_ages_observed": False,
    }
    snapshot = metrics.snapshot()
    assert snapshot["gauges"]["lease.stale"] == 2
    assert "private-key" not in json.dumps(snapshot)


def test_orphan_metric_task_reports_complete_or_unknown(monkeypatch):
    class ObserverSession:
        def close(self):
            pass

    monkeypatch.setattr(ingestion_tasks, "SessionLocal", ObserverSession)
    monkeypatch.setattr(ingestion_tasks, "_build_storage", lambda: object())
    monkeypatch.setattr(
        ingestion_tasks,
        "_bounded_orphan_count",
        lambda _db, _storage, *, limit: 3,
    )
    assert ingestion_tasks.observe_orphan_metrics.run() == {
        "orphan_objects": 3,
        "inventory_complete": True,
    }
    assert metrics.snapshot()["gauges"]["orphan.objects"] == 3

    monkeypatch.setattr(
        ingestion_tasks,
        "_bounded_orphan_count",
        lambda _db, _storage, *, limit: None,
    )
    assert ingestion_tasks.observe_orphan_metrics.run() == {
        "orphan_objects": None,
        "inventory_complete": False,
    }
    assert "orphan.objects" not in metrics.snapshot()["gauges"]


def test_orphan_observer_is_streaming_bounded_and_truthful() -> None:
    class KeyQuery:
        def filter(self, *_criteria):
            return self

        def all(self):
            return [("registered",)]

    class KeySession:
        def query(self, _column):
            return KeyQuery()

    class Storage:
        def __init__(self, keys):
            self.keys = keys

        def iter_keys(self):
            yield from self.keys

    assert (
        ingestion_tasks._bounded_orphan_count(
            KeySession(),
            Storage(["registered", "orphan-a", "orphan-b"]),
            limit=10,
            batch_size=2,
        )
        == 2
    )
    assert (
        ingestion_tasks._bounded_orphan_count(
            KeySession(),
            Storage([f"key-{index}" for index in range(6)]),
            limit=5,
            batch_size=2,
        )
        is None
    )


# --- Tests: happy path --------------------------------------------------------


def test_full_run_completes_and_activates_new_version():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    result = run_ingestion_job(
        db=db,
        job_id=job.id,
        storage=storage,
        extract_text_fn=_stub_extractor(),
        chunk_text_fn=_stub_chunker(["chunk one", "chunk two"]),
        embed_texts_fn=_stub_embedder(),
    )

    assert result["status"] == "completed"
    assert result["chunks"] == 2

    assert job.status == "completed"
    assert job.stage == "activating"
    assert job.finished_at is not None

    assert version.status == "ready"
    assert version.activated_at is not None
    assert version.normalized_artifact_id is not None

    assert document.active_version_id == version.id

    chunks = db.objects.get(Chunk, [])
    assert len(chunks) == 2
    assert {c.version_id for c in chunks} == {version.id}
    assert [c.chunk_index for c in chunks] == [0, 1]

    chunk_embeddings = db.objects.get(ChunkEmbedding, [])
    assert len(chunk_embeddings) == 2

    profiles = db.objects.get(EmbeddingProfile, [])
    assert len(profiles) == 1
    assert profiles[0].is_active is True

    artifacts = db.objects.get(DocumentArtifact, [])
    artifact_types = {a.artifact_type for a in artifacts}
    assert artifact_types == {"original", "normalized_json", "normalized_md"}

    events = db.objects.get(IngestionEvent, [])
    stages_seen = {e.stage for e in events}
    assert stages_seen == set(ingestion_tasks.STAGES)


def test_reruns_all_seven_stages_in_order():
    db = FakeSession()
    storage = FakeStorage()
    _, _, job = _make_chain(db, storage)

    run_ingestion_job(
        db=db,
        job_id=job.id,
        storage=storage,
        extract_text_fn=_stub_extractor(),
        chunk_text_fn=_stub_chunker(["a"]),
        embed_texts_fn=_stub_embedder(),
    )

    events = [e for e in db.objects.get(IngestionEvent, []) if e.status == "started"]
    ordered_stages = [e.stage for e in events]
    assert ordered_stages == list(ingestion_tasks.STAGES)


# --- Tests: idempotency -------------------------------------------------------


def test_rerunning_a_completed_job_is_a_noop_and_creates_no_duplicate_chunks():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    run_ingestion_job(
        db=db,
        job_id=job.id,
        storage=storage,
        extract_text_fn=_stub_extractor(),
        chunk_text_fn=_stub_chunker(["chunk one", "chunk two"]),
        embed_texts_fn=_stub_embedder(),
    )
    assert len(db.objects.get(Chunk, [])) == 2

    result = run_ingestion_job(
        db=db,
        job_id=job.id,
        storage=storage,
        extract_text_fn=_stub_extractor(),
        chunk_text_fn=_stub_chunker(["chunk one", "chunk two"]),
        embed_texts_fn=_stub_embedder(),
    )

    assert result == {"job_id": str(job.id), "status": "completed", "skipped": True}
    # Still exactly 2 — a naive re-run would have appended 2 more.
    assert len(db.objects.get(Chunk, [])) == 2
    assert len(db.objects.get(ChunkEmbedding, [])) == 2


def test_retry_after_partial_failure_clears_stale_chunks_before_reindexing():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    # Simulate a previous attempt that got as far as writing one stale chunk
    # (e.g. died mid-embedding before the job could be marked failed/retried).
    stale_chunk = Chunk(
        id=uuid.uuid4(),
        document_id=document.id,
        version_id=version.id,
        chunk_index=0,
        content="stale leftover chunk from a crashed attempt",
        created_at=datetime.now(timezone.utc),
    )
    db.add(stale_chunk)
    job.status = "queued"  # re-enqueued for retry

    result = run_ingestion_job(
        db=db,
        job_id=job.id,
        storage=storage,
        extract_text_fn=_stub_extractor(),
        chunk_text_fn=_stub_chunker(["fresh one", "fresh two", "fresh three"]),
        embed_texts_fn=_stub_embedder(),
    )

    assert result["status"] == "completed"
    chunks = db.objects.get(Chunk, [])
    assert len(chunks) == 3
    assert stale_chunk not in chunks
    assert all(c.content.startswith("fresh") for c in chunks)


# --- Tests: failure paths must not touch the active version ------------------


def test_missing_version_marks_job_failed():
    db = FakeSession()
    storage = FakeStorage()
    job = IngestionJob(
        id=uuid.uuid4(),
        version_id=uuid.uuid4(),  # no matching DocumentVersion
        status="queued",
        attempt=0,
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)

    with pytest.raises(IngestionJobError):
        run_ingestion_job(db=db, job_id=job.id, storage=storage)

    assert job.status == "failed"
    assert job.error_code == "version_not_found"


def test_unreadable_document_fails_job_without_activating_new_version():
    db = FakeSession()
    storage = FakeStorage()
    old_active_version_id = uuid.uuid4()
    document, version, job = _make_chain(
        db, storage, active_version_id=old_active_version_id
    )

    with pytest.raises(IngestionJobError):
        run_ingestion_job(
            db=db,
            job_id=job.id,
            storage=storage,
            extract_text_fn=_stub_extractor(text="   "),  # blank -> unreadable
            chunk_text_fn=_stub_chunker(["should not be reached"]),
            embed_texts_fn=_stub_embedder(),
        )

    assert job.status == "failed"
    assert version.status == "failed"  # terminal candidate, never activated
    assert version.error_code == "IngestionJobError"
    assert version.activated_at is None
    # Aşama 2 kabul kriteri: the previously active version must keep serving
    # reads until the new one is fully ready.
    assert document.active_version_id == old_active_version_id
    assert len(db.objects.get(Chunk, [])) == 0


def test_embedding_count_mismatch_fails_job():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    with pytest.raises(IngestionJobError):
        run_ingestion_job(
            db=db,
            job_id=job.id,
            storage=storage,
            extract_text_fn=_stub_extractor(),
            chunk_text_fn=_stub_chunker(["one", "two", "three"]),
            embed_texts_fn=lambda texts, instruction="": [[0.1] * 1024],  # too few
        )

    assert job.status == "failed"
    assert version.status == "failed"
    assert version.error_code == "IngestionJobError"
    assert document.active_version_id is None


# --- Tests: stage transition control (Aşama 2.5) ------------------------------


def test_skipping_a_stage_is_rejected():
    db = FakeSession()
    storage = FakeStorage()
    _, _, job = _make_chain(db, storage)

    # ''validating'' -> ''storing'' is fine...
    ingestion_tasks._advance_stage(db, job, "validating")
    # ...but jumping straight to ''embedding'' (skipping ''storing'',
    # ''parsing'', ''chunking'') must be rejected.
    with pytest.raises(StageTransitionError):
        ingestion_tasks._advance_stage(db, job, "embedding")


def test_rewind_to_validating_is_allowed_for_restart():
    db = FakeSession()
    storage = FakeStorage()
    _, _, job = _make_chain(db, storage)

    # Simulate a mid-run state.
    ingestion_tasks._advance_stage(db, job, "validating")
    ingestion_tasks._advance_stage(db, job, "storing")
    assert job.stage == "storing"

    # A retried job may rewind to ''validating'' to restart its pipeline.
    ingestion_tasks._advance_stage(db, job, "validating")
    assert job.stage == "validating"


def test_unknown_stage_is_rejected():
    db = FakeSession()
    storage = FakeStorage()
    _, _, job = _make_chain(db, storage)

    with pytest.raises(StageTransitionError):
        ingestion_tasks._advance_stage(db, job, "not-a-real-stage")


def test_stage_duration_metric_is_bounded_and_has_no_job_or_stage_label():
    job = IngestionJob(
        stage="parsing",
        heartbeat_at=datetime(2026, 9, 6, 10, 0, tzinfo=timezone.utc),
    )
    before = metrics.snapshot()["durations"].get("job.stage", {}).get("count", 0)
    assert ingestion_tasks._record_stage_elapsed(
        job, datetime(2026, 9, 6, 10, 0, 12, tzinfo=timezone.utc)
    )
    after = metrics.snapshot()
    assert after["durations"]["job.stage"]["count"] == before + 1
    assert "parsing" not in str(after)
    assert not ingestion_tasks._record_stage_elapsed(
        job, datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    )


# --- Tests: retryability (Aşama 2.5) ------------------------------------------


def test_transient_failure_is_wrapped_as_retryable():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    def _flaky_embedder(texts, instruction=""):
        raise TimeoutError("embedding gateway timed out")

    with pytest.raises(RetryableIngestionError):
        run_ingestion_job(
            db=db,
            job_id=job.id,
            storage=storage,
            extract_text_fn=_stub_extractor(),
            chunk_text_fn=_stub_chunker(["a"]),
            embed_texts_fn=_flaky_embedder,
        )

    # A transient attempt is explicitly non-terminal so the DB transition
    # guard permits the next Celery attempt to move it back to running.
    assert job.status == "retrying"
    assert job.error_message == "embedding gateway timed out"
    assert job.error_code == "TimeoutError"
    assert job.finished_at is None


def test_permanent_validation_error_is_not_retryable():
    db = FakeSession()
    storage = FakeStorage()
    document, version, job = _make_chain(db, storage)

    def _empty_embedder(texts, instruction=""):
        return []  # produces a count mismatch -> permanent IngestionJobError

    with pytest.raises(IngestionJobError):
        run_ingestion_job(
            db=db,
            job_id=job.id,
            storage=storage,
            extract_text_fn=_stub_extractor(),
            chunk_text_fn=_stub_chunker(["a"]),
            embed_texts_fn=_empty_embedder,
        )
    assert job.status == "failed"
    assert document.active_version_id is None


def test_failed_job_cannot_be_redelivered_as_running():
    db = FakeSession()
    storage = FakeStorage()
    _, _, job = _make_chain(db, storage)
    job.status = "failed"
    with pytest.raises(IngestionJobError, match="terminal"):
        run_ingestion_job(db=db, job_id=job.id, storage=storage)
