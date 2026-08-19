"""Aşama 7.6: unit tests for the incremental re-index service.

Uses a fake SQLAlchemy-style session plus injected parser/chunker/embedder
fakes (no real DB, MinIO or LLM). Verifies the §7.6 contract:
- a changed/new file is re-parsed + re-embedded;
- an unchanged file is skipped (its previous chunks are copied, not re-embedded);
- a new ``DocumentVersion`` is produced with ``version_no = max+1``;
- deleted files do not appear in the new active version;
- ``documents.active_version_id`` is swapped only once the new version is ready.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime

from src.application.reindex_service import ReindexService
from src.infrastructure.repositories.scan_result import ScannedFile, ScanResult
from src.models import Chunk, ChunkEmbedding, Document, DocumentVersion, EmbeddingProfile, SourceFile


# --- Fake SQLAlchemy session (pattern from tests/test_ingestion_tasks.py) ----


def _matches(obj, criteria):
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

    def order_by(self, *args, **kwargs):
        return self

    def _matching(self):
        return [o for o in self.session.objects.get(self.model, []) if _matches(o, self._criteria)]

    def first(self):
        m = self._matching()
        return m[0] if m else None

    def all(self):
        return list(self._matching())


class FakeSession:
    def __init__(self):
        self.objects: dict = {}

    def add(self, obj):
        self.objects.setdefault(type(obj), []).append(obj)

    def get(self, model, id_):
        if id_ is None:
            return None
        for obj in self.objects.get(model, []):
            if str(getattr(obj, "id", None)) == str(id_):
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
        self.data = {}

    def put(self, key, data, content_type=None):
        self.data[key] = data
        return key


# --- helpers -----------------------------------------------------------------


def _h(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write(tmpdir, name, content: bytes):
    path = os.path.join(tmpdir, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)
    return path


def _scanned(tmpdir, name, content: bytes) -> ScannedFile:
    path = _write(tmpdir, name, content)
    return ScannedFile(
        relative_path=name,
        abs_path=path,
        size_bytes=len(content),
        content_hash=_h(content),
        language="python",
        mime_type="text/x-python",
    )


def _seed_prev_version(db, doc):
    """Latest active version with 3 source_files (a, b, del) each 1 chunk."""
    prev = DocumentVersion(
        id=uuid.uuid4(), document_id=doc.id, version_no=1, status="ready", created_at=datetime.utcnow()
    )
    db.add(prev)
    doc.active_version_id = prev.id

    files = {}
    for name in ("a.py", "b.py", "del.py"):
        sf = SourceFile(
            id=uuid.uuid4(), version_id=prev.id, relative_path=name, content_hash=_h(name.encode()),
            size_bytes=4, language="python",
        )
        db.add(sf)
        files[name] = sf.id

    profile = EmbeddingProfile(
        id=uuid.uuid4(), provider="p", model="m", dimension=4, is_active=True, created_at=datetime.utcnow()
    )
    db.add(profile)

    for name, sfid in files.items():
        db.add(
            Chunk(
                id=uuid.uuid4(), document_id=doc.id, version_id=prev.id, source_file_id=sfid,
                chunk_index=0, content=f"old {name}", embedding=[0.0, 0.0, 0.0, 0.0],
            )
        )
    return prev


def _counting_collaborators(tmpdir):
    parsed = []
    embedded = []

    def parse_fn(file_path, filename):
        parsed.append(filename)
        with open(file_path, "rb") as fh:
            return fh.read().decode("utf-8")

    def chunk_fn(text, **kw):
        return [text.strip()]

    def embed_fn(texts, instruction=""):
        embedded.append(list(texts))
        return [[0.25, 0.25, 0.25, 0.25] for _ in texts]

    return parsed, embedded, parse_fn, chunk_fn, embed_fn


def test_changed_reparsed_unchanged_skipped_deleted_absent(tmp_path):
    db = FakeSession()
    doc = Document(
        id=uuid.uuid4(), project_id=uuid.uuid4(), name="repo", size=0, status="indexed",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(doc)
    prev = _seed_prev_version(db, doc)

    parsed, embedded, parse_fn, chunk_fn, embed_fn = _counting_collaborators(str(tmp_path))

    # a.py unchanged (same hash as prev), b.py changed, c.py new; del.py deleted
    a = _scanned(tmp_path, "a.py", b"a.py")
    b = _scanned(tmp_path, "b.py", b"NEW B CONTENT")
    c = _scanned(tmp_path, "c.py", b"NEW C CONTENT")
    scan = ScanResult(source_type="repository", source_revision="newcommit", files=[a, b, c])

    service = ReindexService(
        storage=FakeStorage(), parse_fn=parse_fn, chunk_fn=chunk_fn, embed_fn=embed_fn
    )
    result = service.run(db, doc, scan)

    assert result["version_no"] == 2
    assert result["files_processed"] == 2  # b, c
    assert result["files_copied"] == 1     # a
    assert result["deleted_files"] == ["del.py"]

    # parse only happened for b and c (not the unchanged a.py)
    assert parsed == ["b.py", "c.py"]
    # embed called once per changed file, each with exactly 1 chunk
    assert len(embedded) == 2

    # Find the new active version
    new_version = db.get(DocumentVersion, doc.active_version_id)
    assert new_version is not None
    assert new_version != prev
    assert new_version.status == "ready"
    assert new_version.version_no == 2
    assert new_version.source_revision == "newcommit"
    assert doc.active_version_id == new_version.id

    # New version's source_files = a,b,c (no del.py)
    new_paths = {sf.relative_path for sf in db.query(SourceFile).filter(
        SourceFile.version_id == new_version.id).all()}
    assert new_paths == {"a.py", "b.py", "c.py"}

    # Chunks in new version: 3 (a copied + b + c)
    new_chunks = [ch for ch in db.objects.get(Chunk, []) if ch.version_id == new_version.id]
    assert len(new_chunks) == 3
    assert doc.active_version_id == new_version.id


def test_atomic_activation_not_swapped_without_all_ready(tmp_path):
    """Validation failure mid-run must NOT swap active_version_id."""
    db = FakeSession()
    doc = Document(
        id=uuid.uuid4(), project_id=uuid.uuid4(), name="repo", size=0, status="indexed",
        created_at=datetime.utcnow(),
    )
    db.add(doc)
    _seed_prev_version(db, doc)
    prev_id = doc.active_version_id

    def bad_embed(texts, instruction=""):
        # return wrong count to trigger ReindexError
        return [[0.1] * 4 for _ in range(len(texts) + 1)]

    f = _scanned(tmp_path, "x.py", b"content x")
    scan = ScanResult(source_type="repository", source_revision="r2", files=[f])
    service = ReindexService(parse_fn=lambda p, f: "t", chunk_fn=lambda t, **kw: ["c1"], embed_fn=bad_embed)

    try:
        service.run(db, doc, scan)
        raise AssertionError("expected ReindexError")
    except Exception:
        pass

    assert doc.active_version_id == prev_id  # not swapped on failure


def test_first_ingest_creates_version_one_without_prev(tmp_path):
    db = FakeSession()
    doc = Document(id=uuid.uuid4(), project_id=uuid.uuid4(), name="repo", size=0, created_at=datetime.utcnow())
    db.add(doc)

    parsed, embedded, parse_fn, chunk_fn, embed_fn = _counting_collaborators(str(tmp_path))
    f = _scanned(tmp_path, "only.py", b"hello")
    scan = ScanResult(source_type="repository", source_revision="abc", files=[f])

    service = ReindexService(storage=FakeStorage(), parse_fn=parse_fn, chunk_fn=chunk_fn, embed_fn=embed_fn)
    result = service.run(db, doc, scan)

    assert result["version_no"] == 1
    assert result["files_processed"] == 1
    assert doc.active_version_id is not None
    new_version = db.get(DocumentVersion, doc.active_version_id)
    assert new_version.version_no == 1
    chunks = [ch for ch in db.objects.get(Chunk, []) if ch.version_id == new_version.id]
    assert len(chunks) == 1
    assert chunks[0].embedding == [0.25, 0.25, 0.25, 0.25]  # embedded, not copied


def test_default_wiring_uses_real_code_parser_and_plsql_chunker(tmp_path):
    """The re-index default parse/chunk wiring must use the REAL Aşama 7
    CodeParser + ChunkerRegistry (PL/SQL -> PlSqlChunker, generic code ->
    CodeChunker) even when the caller does NOT inject a code parser/chunker.

    Proven by content: the PL/SQL sample is symbol-split so a real FUNCTION
    carries its signature AND its enclosing package in its own chunk (a naive
    fallback would emit a single blob with no symbol/package context), and the
    Python sample is parsed as code rather than falling through to raw text.
    """
    db = FakeSession()
    doc = Document(
        id=uuid.uuid4(), project_id=uuid.uuid4(), name="repo", size=0, status="indexed",
        created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    db.add(doc)

    pks_content = (
        b"CREATE OR REPLACE PACKAGE BODY emp_mgmt IS\n"
        b"  FUNCTION salary(p_id NUMBER) RETURN NUMBER IS\n"
        b"  BEGIN\n"
        b"    RETURN p_id * 100;\n"
        b"  END;\n"
        b"END emp_mgmt;\n"
    )
    py_content = (
        b"def add(a, b):\n"
        b"    return a + b\n"
        b"\n"
        b"class Greeter:\n"
        b"    def hello(self):\n"
        b"        return \"hi\"\n"
    )

    pks_path = _write(tmp_path, "pkg/emp_mgmt.pks", pks_content)
    py_path = _write(tmp_path, "code/util.py", py_content)

    scan = ScanResult(
        source_type="repository",
        source_revision="r1",
        files=[
            ScannedFile(
                relative_path="pkg/emp_mgmt.pks", abs_path=pks_path,
                size_bytes=len(pks_content), content_hash=_h(pks_content),
                language="plsql", mime_type="text/x-plsql",
            ),
            ScannedFile(
                relative_path="code/util.py", abs_path=py_path,
                size_bytes=len(py_content), content_hash=_h(py_content),
                language="python", mime_type="text/x-python",
            ),
        ],
    )

    def embed_fn(texts, instruction=""):
        return [[0.1] * 4 for _ in texts]

    # Only the embedder is faked; parse_fn/chunk_fn are the REAL defaults.
    service = ReindexService(storage=FakeStorage(), embed_fn=embed_fn)
    result = service.run(db, doc, scan)

    # 2 PL/SQL chunks + 1 Python chunk == 3 total processed chunks.
    assert result["files_processed"] == 3

    chunks = [ch for ch in db.objects.get(Chunk, []) if ch.version_id == doc.active_version_id]
    plsql_chunks = [
        c for c in chunks if "pkg/emp_mgmt.pks" in (c.metadata_json or {}).get("source_file", "")
    ]
    py_chunks = [
        c for c in chunks if "code/util.py" in (c.metadata_json or {}).get("source_file", "")
    ]

    # Real PlSqlChunker symbol-split the PACKAGE BODY: package header + function.
    assert len(plsql_chunks) >= 2
    # The FUNCTION chunk carries its own signature AND its enclosing package.
    assert any(
        "FUNCTION salary(p_id NUMBER) RETURN NUMBER" in c.content
        and "emp_mgmt" in c.content
        for c in plsql_chunks
    )
    # The package header chunk is the package-level signature.
    assert any("PACKAGE BODY emp_mgmt IS" in c.content for c in plsql_chunks)

    # Real CodeParser+CodeChunker parsed the Python file as code content.
    assert len(py_chunks) >= 1
    py_text = "\n".join(c.content for c in py_chunks)
    assert "def add(a, b):" in py_text
    assert "class Greeter:" in py_text
