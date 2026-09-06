"""Read-only post-browser durability/retention check from a fresh DB connection."""

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def verify():
    if urlsplit(os.environ["DATABASE_URL"]).path != "/cv3_a10_browser":
        raise SystemExit("Requires the isolated A10 browser fixture database")
    from sqlalchemy import text
    from src.db import engine
    from src.workers.ingestion_tasks import _build_storage

    with engine.connect() as conn:
        project = conn.execute(
            text(
                "SELECT id FROM projects WHERE name LIKE 'A10 browser %' ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
        counts = dict(
            conn.execute(
                text("""
            SELECT
              (SELECT count(*) FROM documents WHERE project_id=:id AND deleted_at IS NOT NULL) AS soft_deleted_documents,
              (SELECT count(*) FROM messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=:id) AS messages,
              (SELECT count(*) FROM message_claims cl JOIN messages m ON m.id=cl.message_id JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=:id) AS claims,
              (SELECT count(*) FROM message_citations mc JOIN messages m ON m.id=mc.message_id JOIN conversations c ON c.id=m.conversation_id WHERE c.project_id=:id AND mc.evidence_snapshot_encrypted IS NOT NULL) AS encrypted_citations
        """),
                {"id": project},
            )
            .mappings()
            .one()
        )
        keys = list(
            conn.execute(
                text(
                    "SELECT so.storage_key FROM storage_objects so JOIN document_versions v ON v.id=so.version_id JOIN documents d ON d.id=v.document_id WHERE d.project_id=:id AND so.retention_until IS NOT NULL"
                ),
                {"id": project},
            ).scalars()
        )
    storage = _build_storage()
    counts["retained_objects"] = len(keys)
    counts["all_retained_objects_encrypted"] = all(
        storage.is_encrypted(key) for key in keys
    )
    assert counts["messages"] == 2, counts
    assert counts["claims"] == counts["encrypted_citations"] == 1, counts
    assert counts["soft_deleted_documents"] == 1 and len(keys) >= 3, counts
    assert counts["all_retained_objects_encrypted"], counts
    print(
        json.dumps(
            {"result": "PASS", "fresh_connection": True, "counts": counts},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    verify()
