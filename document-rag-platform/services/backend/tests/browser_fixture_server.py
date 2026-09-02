"""Opt-in A10 HTTP fixture: real PG/MinIO/auth/worker core; local deterministic models.

Never a production entrypoint. Run only against the explicitly isolated browser DB.
No golden-derived retrieval or HTTP route mocks. Dispatch is synchronous, not Celery.
"""

import os
import sys
from pathlib import Path
from uuid import UUID, uuid4
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    if (
        os.getenv("CV3_BROWSER_TEST_MODE") != "1"
        or urlsplit(os.environ["DATABASE_URL"]).path != "/cv3_a10_browser"
    ):
        raise SystemExit("Refusing any non-opted-in or non-isolated database")
    os.environ.update(
        {
            "APP_ENV": "test",
            "AUTH_MODE": "api_key",
            "API_KEY_PEPPER": "browser-test-only-pepper-32-characters",
            "LITELLM_BASE_URL": "http://127.0.0.1:1/never-provider",
            "LITELLM_API_KEY": "not-a-provider-key",
            "FEATURE_RERANKER": "false",
            "CHAT_MODEL": "a10-local-deterministic-fixture",
            "CHAT_MODELS": "a10-local-deterministic-fixture",
        }
    )
    from src.api.v1 import chat
    from src.application.ingestion_orchestrator import IngestionOrchestrator
    from src.db import SessionLocal, init_db
    from src.infrastructure.security.auth import hash_api_key
    from src.models import ApiKey, Principal, Workspace, WorkspaceMembership
    from src.workers.ingestion_tasks import _build_storage, process_ingestion_job
    from src.config import settings
    from src.main import app
    import uvicorn

    init_db()
    workspace_id = UUID("22222222-2222-4222-8222-222222222222")
    principal_id = UUID("55555555-5555-4555-8555-555555555555")
    raw_key = "test-live-browser-memory-only-0000"
    with SessionLocal() as db:
        if db.get(Workspace, workspace_id) is None:
            db.add(Workspace(id=workspace_id, name="A10 isolated browser workspace"))
        if db.get(Principal, principal_id) is None:
            db.add(
                Principal(id=principal_id, subject="a10-browser-test", is_active=True)
            )
        db.flush()
        if (
            db.query(WorkspaceMembership)
            .filter_by(workspace_id=workspace_id, principal_id=principal_id)
            .first()
            is None
        ):
            db.add(
                WorkspaceMembership(
                    workspace_id=workspace_id, principal_id=principal_id, role="admin"
                )
            )
        if db.query(ApiKey).filter_by(key_prefix=raw_key[:12]).first() is None:
            db.add(
                ApiKey(
                    id=uuid4(),
                    principal_id=principal_id,
                    key_prefix=raw_key[:12],
                    key_hash=hash_api_key(raw_key, settings.API_KEY_PEPPER),
                )
            )
        db.commit()

    class LocalAnswerer:
        is_remote = False

        def complete_structured(self, system, user, *, schema, model=None):
            return {
                "answerable": True,
                "no_answer_reason": None,
                "answer_text": "PAYMENT_FLAG equals 1 when payment is complete.",
                "claims": [
                    {
                        "claim_text": "PAYMENT_FLAG equals 1 when payment is complete.",
                        "source_labels": ["S1"],
                    }
                ],
                "used_source_labels": ["S1"],
                "uncertainty": [],
                "safety_flags": [],
            }

    def dispatch(job_id, inbox_idempotency_key=None):
        with SessionLocal() as db:
            return IngestionOrchestrator(db, _build_storage()).process_job(
                job_id,
                embed_texts_fn=lambda texts, instruction="": [
                    [0.01] * 1024 for _ in texts
                ],
                embedding_is_remote=False,
                worker_id="a10-local-browser-worker",
                inbox_idempotency_key=inbox_idempotency_key,
            )

    chat.chat_client = LocalAnswerer()
    chat.embed_text = lambda *args, **kwargs: [0.01] * 1024
    process_ingestion_job.delay = dispatch
    uvicorn.run(
        app, host="127.0.0.1", port=44999, log_level="warning", access_log=False
    )


if __name__ == "__main__":
    main()
