"""Aşama 7.5: PlSqlChunker — symbol-aware PL/SQL chunking.

Covers: boundary detection that never splits inside strings/comments, per-chunk
signature + enclosing-package metadata, header-based embedding_text (file path +
language + symbol + signature), oversized-procedure splitting at inner block
boundaries that re-adds symbol context (via a parent chunk), and registry
routing for code sources.
"""

from src.domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from src.infrastructure.chunkers.plsql_chunker import PlSqlChunker, strip_plsql_lines
from src.infrastructure.chunkers.registry import ChunkerRegistry
from src.infrastructure.parsers.code_parser import CodeParser

PKG_TEXT = (
    "CREATE OR REPLACE PACKAGE emp_pkg IS\n"
    "  -- declaration comment mentioning PROCEDURE raise_salary\n"
    "  PROCEDURE raise_salary (p_empno NUMBER);\n"
    "END emp_pkg;\n"
    "/\n"
    "CREATE OR REPLACE PACKAGE BODY emp_pkg IS\n"
    "  -- a comment containing the word PROCEDURE should NOT open a boundary\n"
    "  PROCEDURE raise_salary (p_empno NUMBER) IS\n"
    "    v_note VARCHAR2(200) := 'string mentions PROCEDURE raise_salary too';\n"
    "  BEGIN\n"
    "    UPDATE emp SET sal = sal * 1.1 WHERE empno = p_empno;\n"
    "    DBMS_OUTPUT.PUT_LINE('done with PROCEDURE');\n"
    "  END raise_salary;\n"
    "END emp_pkg;\n"
    "/\n"
)

BIG_PROC = (
    "CREATE OR REPLACE PROCEDURE big_proc IS\n"
    "BEGIN\n"
    "  FOR i IN 1..1000 LOOP\n"
    "    INSERT INTO t VALUES(i);\n"
    "    INSERT INTO t2 VALUES(i * 2);\n"
    "  END LOOP;\n"
    "  IF v > 0 THEN\n"
    "    v := v + 1;\n"
    "  END IF;\n"
    "END big_proc;\n"
    "/\n"
)


class FakeTokenCounter:
    """Duck-typed TokenCounter receiver: naive characters/4."""

    def count(self, text: str) -> int:
        return max(1, len(text or "") // 4)


def _plsrc(text, *, language="plsql", source_id="plsrc-1", file_path="emp.pks"):
    unit = ContentUnit(
        unit_id="code:1",
        unit_type=UnitType.CODE,
        text=text,
        order=1,
        hierarchy=Hierarchy(heading_path=["emp.pks"]),
        locator=SourceLocator(file_path=file_path, line_start=1, line_end=len(text.splitlines())),
        metadata={"language": language},
    )
    return NormalizedSource(
        source_id=source_id,
        source_type="code",
        title="emp.pks",
        language=language,
        metadata={"file_path": file_path, "language": language},
        units=[unit],
    )


def _chunker(max_tokens=None):
    return PlSqlChunker(counter=FakeTokenCounter(), max_tokens=max_tokens)


# --- boundary detection + no false splits ----------------------------------


def test_strip_removes_keywords_inside_strings_and_comments():
    cleaned = strip_plsql_lines(
        "-- PROCEDURE in comment\n"
        "x := 'PROCEDURE in string';\n"
        "/* block with PROCEDURE */\n"
        "PROCEDURE real_proc IS\n"
    )
    # the only line that still contains a declaration keyword is the real one
    assert "PROCEDURE" in cleaned[3]
    assert "PROCEDURE" not in cleaned[0]
    assert "PROCEDURE" not in cleaned[1]
    assert "PROCEDURE" not in cleaned[2]


def test_package_body_boundaries_no_false_split_in_string_or_comment():
    source = _plsrc(PKG_TEXT, file_path="emp.pks")
    chunks = _chunker().chunk_source(source)

    procs = [c for c in chunks if c.metadata.get("symbol_type") == "PROCEDURE"]
    # exactly one procedure boundary: the "PROCEDURE" words inside the comment
    # and the string must NOT create extra splits
    assert len(procs) == 1
    proc = procs[0]

    assert proc.content.count("PROCEDURE") >= 2  # signature + string/comment intact
    assert "string mentions PROCEDURE raise_salary too" in proc.content
    assert "done with PROCEDURE" in proc.content

    # package spec + package body header also chunked separately
    types = {c.metadata.get("symbol_type") for c in chunks}
    assert "PACKAGE" in types
    assert "PACKAGE BODY" in types


def test_each_chunk_carries_signature_and_enclosing_package():
    source = _plsrc(PKG_TEXT, file_path="emp.pks")
    chunks = _chunker().chunk_source(source)
    proc = [c for c in chunks if c.metadata.get("symbol_type") == "PROCEDURE"][0]

    loc = proc.locator
    assert loc["symbol_name"] == "RAISE_SALARY"
    assert loc["symbol_type"] == "PROCEDURE"
    assert loc["file_path"] == "emp.pks"
    assert proc.metadata["enclosing_package"] == "EMP_PKG"
    assert "PROCEDURE raise_salary" in proc.metadata["signature"]


def test_embedding_text_has_header_with_file_language_symbol_signature():
    source = _plsrc(PKG_TEXT, file_path="emp.pks")
    chunks = _chunker().chunk_source(source)
    proc = [c for c in chunks if c.metadata.get("symbol_type") == "PROCEDURE"][0]

    assert "File: emp.pks" in proc.embedding_text
    assert "Language: plsql" in proc.embedding_text
    assert "Symbol: RAISE_SALARY" in proc.embedding_text
    assert "Type: PROCEDURE" in proc.embedding_text
    assert "Enclosing: EMP_PKG" in proc.embedding_text
    assert "Signature: PROCEDURE raise_salary" in proc.embedding_text
    # the raw content must NOT contain the injected header
    assert "# File:" not in proc.content


# --- oversized procedure split ---------------------------------------------


def test_oversized_procedure_splits_at_inner_blocks_and_keeps_context():
    source = _plsrc(BIG_PROC, file_path="big.sql")
    chunks = _chunker(max_tokens=25).chunk_source(source)

    parent = [c for c in chunks if c.metadata.get("chunk_kind") == "symbol_parent"]
    inners = [c for c in chunks if c.metadata.get("chunk_kind") == "inner_split"]

    assert len(parent) == 1
    assert parent[0].metadata["symbol_name"] == "BIG_PROC"
    assert parent[0].parent_chunk_id is None
    assert parent[0].content == BIG_PROC.rstrip("\n")

    assert len(inners) >= 2
    parent_id = parent[0].chunk_id
    for child in inners:
        assert child.parent_chunk_id == parent_id
        assert child.token_count <= 25
        # signature + enclosing symbol context re-added to every inner chunk
        assert "Signature: CREATE OR REPLACE PROCEDURE big_proc IS" in child.embedding_text
        assert "Symbol: BIG_PROC" in child.embedding_text
        # inner chunks only ever start at inner-block boundaries
        first_line = child.content.splitlines()[0].strip()
        assert first_line in (
            "CREATE OR REPLACE PROCEDURE big_proc IS",
            "BEGIN",
            "FOR i IN 1..1000 LOOP",
            "END LOOP;",
            "IF v > 0 THEN",
        )


def test_empty_or_no_code_unit_returns_empty():
    empty = NormalizedSource(source_id="x", source_type="code", language="plsql", units=[])
    assert _chunker().chunk_source(empty) == []


# --- registry routing ------------------------------------------------------


def test_registry_routes_plsql_code_source():
    source = _plsrc(PKG_TEXT, file_path="emp.pks")
    chunks = ChunkerRegistry(
        token_counter=FakeTokenCounter(),
        max_tokens=900,
        target_tokens=600,
        min_tokens=250,
        parent_max_tokens=2400,
    ).chunk(source)

    assert all(c.chunk_type == "code" for c in chunks)
    seqs = [c.sequence_no for c in chunks]
    assert seqs == sorted(seqs)
    # PL/SQL metadata present
    proc = [c for c in chunks if c.metadata.get("symbol_type") == "PROCEDURE"]
    assert proc and proc[0].metadata["enclosing_package"] == "EMP_PKG"


def test_registry_generic_python_code_routes_with_symbol_header(tmp_path):
    path = tmp_path / "sample.py"
    path.write_text(
        "def one():\n    return 1\n\n\ndef two():\n    return 2\n",
        encoding="utf-8",
    )
    source = CodeParser().parse(str(path), "sample.py")
    chunks = ChunkerRegistry(token_counter=FakeTokenCounter(), max_tokens=900).chunk(source)

    assert all(c.chunk_type == "code" for c in chunks)
    assert any("Language: python" in c.embedding_text for c in chunks)
