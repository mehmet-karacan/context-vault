"""Aşama 4 tests: content-sensitive ChunkerRegistry.

Uses an injected fake ``TokenCounter`` (``len(text)//4``) so the tests never
depend on the concrete, concurrently-built ``token_counter.py`` module. Covers:
token-bounded sizes, heading-context in embedding_text (not raw content),
no mid-cell table splits + header repetition, code blocks not sliced like
paragraphs, parent-child links, monotonic sequence_no, stable content_hash,
and per-unit-type dispatch.
"""

import hashlib

from src.domain.normalized_content import (
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)
from src.infrastructure.chunkers.registry import ChunkerRegistry


class FakeTokenCounter:
    """Duck-typed TokenCounter receiver: naive characters/4."""

    def count(self, text: str) -> int:
        return max(1, len(text or "") // 4)


def _counter():
    return FakeTokenCounter()


def _source(units, source_id="src-1"):
    return NormalizedSource(
        source_id=source_id,
        source_type="markdown",
        units=units,
    )


def _doc_unit(unit_id, text, order, heading_path=()):
    return ContentUnit(
        unit_id=unit_id,
        unit_type=UnitType.PARAGRAPH,
        text=text,
        markdown=text,
        order=order,
        hierarchy=Hierarchy(heading_path=list(heading_path), depth=len(heading_path)),
        locator=SourceLocator(file_path="doc.md", line_start=order, line_end=order),
    )


def _heading_unit(unit_id, text, order, heading_path):
    return ContentUnit(
        unit_id=unit_id,
        unit_type=UnitType.HEADING,
        text=text,
        markdown=f"# {text}",
        order=order,
        hierarchy=Hierarchy(heading_path=list(heading_path), depth=len(heading_path)),
        locator=SourceLocator(file_path="doc.md", line_start=order, line_end=order),
    )


def _table_unit(unit_id, header, rows, order):
    lines = [header, "|---|---|"] + rows
    return ContentUnit(
        unit_id=unit_id,
        unit_type=UnitType.TABLE,
        text="\n".join(lines),
        markdown="\n".join(lines),
        order=order,
        hierarchy=Hierarchy(heading_path=["Tablolar"]),
        locator=SourceLocator(file_path="doc.md", line_start=order, line_end=order + len(lines)),
        metadata={"row_count": len(lines)},
    )


def _code_unit(unit_id, code, order, heading_path=()):
    return ContentUnit(
        unit_id=unit_id,
        unit_type=UnitType.CODE,
        text=code,
        markdown=f"```python\n{code}\n```",
        order=order,
        hierarchy=Hierarchy(heading_path=list(heading_path)),
        locator=SourceLocator(file_path="code.py", line_start=order, line_end=order + 5),
        metadata={"language": "python"},
    )


def _simple_registry(**kwargs):
    defaults = {
        "token_counter": _counter(),
        "target_tokens": 600,
        "min_tokens": 250,
        "max_tokens": 900,
        "overlap_ratio": 0.12,
        "parent_max_tokens": 2400,
    }
    defaults.update(kwargs)
    return ChunkerRegistry(**defaults)


def _paragraphs(n, text_len=200, heading_path=("KIRKBEŞİNCİBÖLÜM",)):
    units = [_heading_unit("h1", heading_path[-1], 0, list(heading_path))]
    for i in range(n):
        body = f"paragraf-{i}: " + ("x" * text_len)
        units.append(_doc_unit(f"p{i}", body, i + 1, heading_path))
    return units


# --- token bounds + unit boundaries ---------------------------------------


def test_document_chunks_are_token_bounded_and_respect_unit_boundaries():
    source = _source(_paragraphs(60, text_len=100))
    chunks = [
        c
        for c in _simple_registry().chunk(source)
        if c.chunk_type == "document"
    ]
    assert len(chunks) >= 2
    for c in chunks:
        assert c.token_count <= 900
    # every paragraph survived whole (no mid-unit slicing)
    for p in [u for u in source.units if u.unit_type == UnitType.PARAGRAPH]:
        assert p.text in "".join(c.content for c in chunks)


def test_document_chunks_respect_min_tokens_except_final_remainder():
    chunks = [
        c
        for c in _simple_registry().chunk(_source(_paragraphs(80))).__iter__()
    ]
    docs = [c for c in chunks if c.chunk_type == "document"]
    for c in docs[:-1]:
        assert c.token_count >= 250
    for c in docs:
        assert c.token_count <= 900


# --- heading context vs raw content ---------------------------------------


def test_heading_context_in_embedding_text_but_not_raw_content():
    source = _source(_paragraphs(5))
    chunks = [
        c
        for c in _simple_registry().chunk(source)
        if c.chunk_type == "document"
    ]
    assert chunks
    for c in chunks:
        assert "KIRKBEŞİNCİBÖLÜM" in c.embedding_text
        assert "KIRKBEŞİNCİBÖLÜM" not in c.content
        assert c.heading_path == ["KIRKBEŞİNCİBÖLÜM"]


# --- table row-grouping + header repetition --------------------------------


def test_large_table_split_into_row_groups_with_repeated_header():
    header = "| NAME | VALUE |"
    rows = [f"| row-{i:03d} | {'v' * 24} |" for i in range(50)]
    unit = _table_unit("t1", header, rows, 100)
    source = _source([unit])
    # small max forces multiple groups while each whole row stays intact
    chunks = [
        c
        for c in _simple_registry(max_tokens=12).chunk(source)
        if c.chunk_type == "table"
    ]
    assert len(chunks) >= 3
    for c in chunks:
        lines = [ln for ln in c.content.splitlines() if ln.strip()]
        # header (and separator) repeated at the top of every group
        assert lines[0].strip() == "| NAME | VALUE |"
        assert lines[1].strip() == "|---|---|"
        # no cell split: every non-header line is one of the original rows
        for ln in lines[2:]:
            assert ln.strip() in rows


def test_small_table_kept_as_single_group():
    unit = _table_unit("t1", "| A | B |", ["| 1 | 2 |"], 100)
    chunks = [
        c
        for c in _simple_registry().chunk(_source([unit]))
        if c.chunk_type == "table"
    ]
    assert len(chunks) == 1


# --- code handling ---------------------------------------------------------


def test_code_block_not_sliced_like_paragraph():
    code = "\n".join(
        f"def func_{i}():\n    return {i}" for i in range(20)
    )
    unit = _code_unit("c1", code, 50, heading_path=["Kodlar"])
    chunks = [
        c
        for c in _simple_registry().chunk(_source([unit]))
        if c.chunk_type == "code"
    ]
    assert chunks
    assert len(chunks) == 1
    assert chunks[0].content == code


# --- parent-child, sequence, hash, dispatch --------------------------------


def test_parent_child_relationship_represented():
    source = _source(_paragraphs(80))
    chunks = _simple_registry().chunk(source)
    parents = [c for c in chunks if c.chunk_type == "parent"]
    children = [c for c in chunks if c.chunk_type != "parent"]
    assert parents
    parent_ids = {p.chunk_id for p in parents}
    assert all(c.parent_chunk_id in parent_ids for c in children)
    for p in parents:
        assert p.parent_chunk_id is None


def test_sequence_no_is_monotonic():
    chunks = _simple_registry().chunk(_source(_paragraphs(60)))
    seqs = [c.sequence_no for c in chunks]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_content_hash_is_stable_for_identical_content():
    a = _simple_registry().chunk(_source(_paragraphs(10)))
    b = _simple_registry().chunk(_source(_paragraphs(10)))
    ha = {c.content_hash: c.content for c in a if c.chunk_type == "document"}
    hb = {c.content_hash: c.content for c in b if c.chunk_type == "document"}
    assert ha == hb
    for digest, content in ha.items():
        assert digest == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_registry_dispatches_table_code_and_document_by_unit_type():
    source = _source(
        [
            _heading_unit("h", "Başlık", 0, ["Başlık"]),
            _doc_unit("p", "bir paragraf", 1, ["Başlık"]),
            _table_unit("t", "| A |", ["| 1 |"], 2),
            _code_unit("c", "x = 1", 3),
        ]
    )
    chunks = _simple_registry().chunk(source)
    types = {c.chunk_type for c in chunks}
    assert "document" in types
    assert "table" in types
    assert "code" in types


def test_every_chunk_carries_chunker_profile_metadata():
    chunks = _simple_registry().chunk(_source(_paragraphs(20)))
    for c in chunks:
        assert "chunker_profile" in c.metadata
        assert c.metadata["chunker_profile"]["name"] == "context-vault-chunker"
        assert c.metadata["chunker_profile"]["token_counter_method"] == "injected"
