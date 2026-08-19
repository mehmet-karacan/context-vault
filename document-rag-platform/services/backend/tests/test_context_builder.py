"""Aşama 5.5 tests: ContextBuilder context expansion.

Uses an injected fake ``TokenCounter`` (duck-typed ``count(text)``) so tests
never depend on the concrete counter or a database. Covers: parent + adjacent
expansion, dedup by content_hash, token-budget and chunk-cap enforcement, table
header / code signature context, and determinism.
"""

from src.infrastructure.chunkers.base import ChunkCandidate
from src.infrastructure.retrieval.context_builder import (
    ContextBuilder,
    context_item_from,
)


class FakeTokenCounter:
    def count(self, text: str) -> int:
        return max(1, len(text or "") // 4)


def _counter(**overrides):
    c = {"divisor": 4}
    c.update(overrides)
    return FakeTokenCounter() if not overrides else _FixedCounter(c["divisor"])


class _FixedCounter:
    def __init__(self, divisor):
        self.divisor = divisor

    def count(self, text):
        return max(1, len(text or "") // self.divisor)


def _chunk(chunk_id, content, *, seq=0, source="src-1", ctype="document",
           parent=None, metadata=None, heading=()):
    return ChunkCandidate(
        chunk_id=chunk_id,
        source_id=source,
        chunk_type=ctype,
        content=content,
        embedding_text=content,
        heading_path=list(heading),
        locator={"file_path": f"{source}.md", "block_index": seq},
        parent_chunk_id=parent,
        sequence_no=seq,
        metadata=dict(metadata or {}),
    )


def _neighbor_map(chunks):
    return {c.sequence_no: c for c in chunks}


def test_parent_expansion_adds_parent_chunk():
    parent = _chunk("chunk-parent", "SECTION OVERVIEW: long parent context")
    child = _chunk("chunk-a", "detail paragraph content", seq=5, parent="chunk-parent")
    pool = {"chunk-parent": parent}

    result = ContextBuilder(token_counter=_counter()).build([child], chunk_pool=pool)

    relations = [i.relation for i in result.items]
    assert relations == ["selected", "parent"]
    assert result.items[0].chunk_id == "chunk-a"
    assert result.items[1].chunk_id == "chunk-parent"
    assert result.items[1].content == parent.content


def test_adjacent_expansion_adds_controlled_neighbours():
    chunks = {
        1: _chunk("c1", "content one", seq=1),
        2: _chunk("c2", "content two", seq=2),
        3: _chunk("c3", "content three", seq=3),
        4: _chunk("c4", "content four", seq=4),
    }
    selected = chunks[2]
    resolver = lambda source, seq: chunks.get(seq)

    result = ContextBuilder(
        token_counter=_counter(), adjacent_window=1
    ).build([selected], neighbor_resolver=resolver)

    rel = {i.chunk_id: i.relation for i in result.items}
    assert rel["c1"] == "adjacent"
    assert rel["c2"] == "selected"
    assert rel["c3"] == "adjacent"
    # adjacency window = 1 -> c4 not pulled in.
    assert "c4" not in rel


def test_dedup_same_content_not_repeated():
    dup1 = _chunk("a1", "identical text appears twice", seq=1)
    dup2 = _chunk("a2", "identical text appears twice", seq=9)
    other = _chunk("b1", "different text", seq=3)

    result = ContextBuilder(token_counter=_counter()).build([dup1, dup2, other])

    contents = [i.content for i in result.items]
    assert len(contents) == len(set(contents))
    assert result.items[0].chunk_id == "a1"
    # dedup by content_hash -> a2 dropped, b1 kept.
    ids = [i.chunk_id for i in result.items]
    assert ids == ["a1", "b1"]


def test_dedup_expansion_not_repeated():
    parent = _chunk("par", "parent text that equals selected")
    child = _chunk("child", "parent text that equals selected", parent="par")

    result = ContextBuilder(token_counter=_counter()).build([child], chunk_pool={"par": parent})

    assert len(result.items) == 1
    assert result.items[0].relation == "selected"


def test_token_budget_respected_with_fake_counter():
    # Each chunk counts as exactly 100 tokens via a fake counted content.
    counter = _ExactCounter()
    builder = ContextBuilder(token_counter=counter, max_tokens=250)
    chunks = [_chunk(f"c{i}", f"chunk-{i}-" + "x" * 90, seq=i) for i in range(6)]

    result = builder.build(chunks)

    assert result.total_tokens <= 250
    # 100 + 100 = 200 fits; third (300) exceeds budget -> stops.
    assert len(result.items) == 2
    assert result.truncated is True


class _ExactCounter:
    """Each counted string yields 100 tokens to make budget math obvious."""

    def count(self, text):
        return 100


def test_chunk_cap_respected():
    counter = _ExactCounter()
    builder = ContextBuilder(token_counter=counter, max_chunks=2, max_tokens=10_000)
    chunks = [_chunk(f"c{i}", f"content {i}", seq=i) for i in range(5)]

    result = builder.build(chunks)

    assert len(result.items) == 2
    assert result.max_chunks == 2
    assert result.truncated is True


def test_table_header_context_included():
    table = _chunk(
        "t1",
        "| 1 | 2 |",
        ctype="table",
        metadata={"header": "| A | B |", "header_repeated": False},
    )

    result = ContextBuilder(token_counter=_counter()).build([table])

    relations = [i.relation for i in result.items]
    assert relations == ["table_header", "selected"]
    assert result.items[0].content == "| A | B |"


def test_table_header_not_duplicated_when_embedded():
    content = "| A | B |\n| 1 | 2 |"
    table = _chunk(
        "t1",
        content,
        ctype="table",
        metadata={"header": "| A | B |", "header_repeated": True},
    )

    result = ContextBuilder(token_counter=_counter()).build([table])

    # Header already in content -> no extra table_header item.
    assert [i.relation for i in result.items] == ["selected"]


def test_code_signature_context_included():
    code = _chunk(
        "k1",
        "    return a + b",
        ctype="code",
        metadata={"signature": "def add(a: int, b: int):"},
    )

    result = ContextBuilder(token_counter=_counter()).build([code])

    relations = [i.relation for i in result.items]
    assert relations == ["code_signature", "selected"]
    assert result.items[0].content == "def add(a: int, b: int):"
    assert result.items[0].chunk_type == "code"


def test_deterministic_output():
    chunks = [
        _chunk("c1", "first content", seq=1),
        _chunk("c2", "second content", seq=2, parent="par"),
    ]
    pool = {"par": _chunk("par", "parent for second", seq=1)}
    resolver = lambda source, seq: None

    builder = ContextBuilder(token_counter=_counter())
    r1 = builder.build(chunks, chunk_pool=pool, neighbor_resolver=resolver)
    r2 = builder.build(list(chunks), chunk_pool=dict(pool), neighbor_resolver=resolver)

    assert [i.chunk_id for i in r1.items] == [i.chunk_id for i in r2.items]
    assert [i.content for i in r1.items] == [i.content for i in r2.items]
    assert r1.total_tokens == r2.total_tokens


def test_content_prefers_raw_over_embedding_text():
    chunk = ChunkCandidate(
        chunk_id="x1",
        source_id="src",
        chunk_type="document",
        content="RAW content body",
        embedding_text="# HEADER\n\nRAW content body",
        sequence_no=0,
    )

    item = context_item_from(chunk)

    assert item.content == "RAW content body"
    assert "HEADER" not in item.content


def test_accepts_generic_dict_candidates():
    # retrieval/base.py is a concurrent worker's file; the builder must accept
    # plain dicts carrying chunk info (RetrievalCandidate may not exist yet).
    d = {
        "chunk": {
            "chunk_id": "d1",
            "source_id": "src",
            "chunk_type": "document",
            "content": "dict based content",
            "embedding_text": "dict based content",
            "heading_path": [],
            "locator": {},
            "content_hash": "abc123",
            "sequence_no": 0,
        }
    }

    result = ContextBuilder(token_counter=_counter()).build([d])

    assert len(result.items) == 1
    assert result.items[0].chunk_id == "d1"
    assert result.items[0].content == "dict based content"
