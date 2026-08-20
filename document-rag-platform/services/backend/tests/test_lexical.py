"""Aşama 5.2: unit tests for LexicalRetriever (DB-free).

Verifies candidate count (config default + override), the ``simple`` text-search
config choice, filter application inside the spec and generated SQL, and the
result shape via a fake session.
"""

from __future__ import annotations

from src.infrastructure.retrieval import LexicalRetriever, lexical_sql_from_spec
from src.infrastructure.retrieval.lexical import (
    content_has_any_term,
    filter_query_terms,
    significant_query_terms,
)


class _Row:
    def __init__(self, chunk_id, score):
        self.chunk_id = chunk_id
        self.score = score


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.executed_sql = None
        self.executed_params = None

    def execute(self, stmt, params=None):
        self.executed_sql = str(stmt)
        self.executed_params = dict(params or {})
        return self

    def fetchall(self):
        return self.rows


def test_candidate_k_defaults_and_override():
    assert LexicalRetriever().candidate_k == 40  # settings.LEXICAL_CANDIDATE_K
    assert LexicalRetriever(candidate_k=10).candidate_k == 10


def test_simple_ts_config_by_default_and_override():
    assert LexicalRetriever().ts_config == "simple"
    assert LexicalRetriever(ts_config="turkish").ts_config == "turkish"


def test_spec_candidate_k_and_config():
    spec = LexicalRetriever().build_spec("hello", top_k=8)
    assert spec["candidate_k"] == 8
    assert spec["ts_config"] == "simple"
    assert spec["query_text"] == "hello"


def test_filter_query_terms_keeps_real_terms():
    # Cross-lingual filler must not veto the meaningful acronym term.
    assert filter_query_terms("what is stp?") == "stp"
    assert filter_query_terms("what is STP?") == "STP"
    assert filter_query_terms("STP nedir") == "STP"
    # Non-filler / multi-term content queries are effectively unchanged.
    assert filter_query_terms("PAYMENT_FLAG") == "PAYMENT_FLAG"
    assert filter_query_terms("talep tablosu") == "talep tablosu"


def test_build_spec_strips_fillers_from_query_text():
    spec = LexicalRetriever().build_spec("what is stp?", top_k=8)
    assert spec["query_text"] == "stp"


def test_significant_query_terms_reconstruct_split_acronyms():
    # "ttnet sis" and "tt sis" are split acronyms; the content lexemes are the
    # contiguous "ttnetsis"/"ttsis". significant_query_terms yields both the
    # filtered tokens and the joined adjacent-pair forms so content-verified
    # presence can match the single lexeme that actually appears in the source.
    terms = significant_query_terms("ttnet sis ve tt sis arasındaki farklar nelerdir")
    assert "ttnetsis" in terms  # joined "ttnet"+"sis"
    assert "ttsis" in terms  # joined "tt"+"sis"
    assert "ve" not in terms  # connector stopword stripped
    assert "nelerdir" not in terms  # question filler stripped
    assert terms == list(dict.fromkeys(terms))  # de-duplicated


def test_significant_query_terms_empty_input():
    assert significant_query_terms("") == []
    assert significant_query_terms("ve ve") == []


def test_content_has_any_term_matches_single_lexeme():
    # A chunk whose content holds the contiguous acronym must count as lexical
    # presence even though the caller phrased it with a space.
    terms = significant_query_terms("ttnet sis ile tt sis arasındaki fark")
    assert content_has_any_term(
        "Mevcut UP10 sisteminin TTNETSIS sistemine aktarılması", terms
    ) is True
    # A chunk with no significant term in its text has no presence.
    assert content_has_any_term(
        "Tablo üzerinde proje türü filtrelenebilir", terms
    ) is False
    assert content_has_any_term("", terms) is False
    assert content_has_any_term("herhangi bir metin", []) is False


def test_sql_uses_simple_config_and_filters():
    spec = LexicalRetriever().build_spec(
        "PAYMENT_FLAG", top_k=15, filters={"project_id": "proj-1"}
    )
    sql, params = lexical_sql_from_spec(spec)
    assert "plainto_tsquery('simple', :query_text)" in sql
    assert "query @@ chunks.search_vector" in sql
    assert "ts_rank_cd(chunks.search_vector, query) AS score" in sql
    assert "WHERE query @@ chunks.search_vector AND d.project_id = :fp0" in sql
    assert params["query_text"] == "PAYMENT_FLAG"
    assert params["fp0"] == "proj-1"
    assert params["candidate_k"] == 15


def test_search_returns_candidate_shape_via_fake_session():
    session = FakeSession(rows=[_Row("chunk-a", 0.88), _Row("chunk-b", 0.6)])
    retriever = LexicalRetriever(session=session)
    results = retriever.search("billing", top_k=5)
    assert [c.chunk_id for c in results] == ["chunk-a", "chunk-b"]
    assert {c.source for c in results} == {"lexical"}
    assert results[0].rank == 1
    assert session.executed_sql is not None
