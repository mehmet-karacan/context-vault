"""Aşama 5.6 tests: no-answer / intent separation.

Confirms the three-way behaviour (small-talk / document+evidence /
document-no-evidence), that an empty retrieval is never auto-small-talk, that
lexical/identifier evidence can rescue a low dense score, and that the policy
relies on configurable thresholds rather than a single hardcoded 0.55.
"""

import pytest

from src.infrastructure.retrieval.no_answer import (
    INTENT_DOCUMENT,
    INTENT_SMALLTALK,
    AnswerPolicy,
    EvidenceSignal,
    is_smalltalk,
)


def _dense(score):
    return {"dense_score": score}


def _lex(score):
    return {"lexical_score": score}


def test_smalltalk_classified_separately():
    policy = AnswerPolicy()
    decision = policy.classify("Merhaba, nasılsın?")

    assert decision.intent == INTENT_SMALLTALK
    assert decision.answerable is False


def test_document_question_not_smalltalk():
    policy = AnswerPolicy()
    decision = policy.classify(
        "STP'de anchor user tablosuna tanımlanan müşteriler için 3C00 RN ne ifade eder?",
        evidence=[_dense(0.72)],
    )

    assert decision.intent == INTENT_DOCUMENT
    assert decision.answerable is True


def test_empty_retrieval_is_not_smalltalk():
    policy = AnswerPolicy()
    query = "STP sisteminin yıllık lisans maliyeti ve destek anlaşması bedeli ne kadardır?"

    decision = policy.classify(query, evidence=[])

    assert decision.intent == INTENT_DOCUMENT
    assert decision.answerable is False
    assert "no retrieval evidence" in decision.reason


def test_pure_greeting_with_short_query_is_smalltalk():
    assert is_smalltalk("selam") is True
    assert is_smalltalk("merhaba mrb") is True


def test_empty_string_is_smalltalk_but_question_is_not():
    assert is_smalltalk("") is True
    assert is_smalltalk("  ") is True


def test_lexical_evidence_rescues_low_dense_score():
    policy = AnswerPolicy()  # default score_threshold 0.55
    evidence = [
        {"dense_score": 0.2, "lexical_score": 0.85},
    ]

    decision = policy.classify("PAYMENT_FLAG 1 olduğunda ne olur?", evidence=evidence)

    assert decision.answerable is True
    assert "lexical" in decision.reason


def test_lexical_presence_rescues_near_threshold_dense_score():
    # A cross-lingual short-acronym query surfaces a dense score just under the
    # 0.55 threshold (ts_rank_cd of one acronym in a large chunk is tiny, so
    # LEXICAL_STRONG_SCORE never fires). Because the term is *present* in the
    # retrieved content (lexical_score is not None) and the dense score clears
    # the presence floor, the real evidence must not be rejected.
    policy = AnswerPolicy()  # default presence floor 0.45
    evidence = [
        {"dense_score": 0.548, "lexical_score": 0.002},
    ]

    decision = policy.classify("what is stp?", evidence=evidence)

    assert decision.answerable is True
    assert decision.intent == INTENT_DOCUMENT
    assert decision.inputs["has_lexical_presence"] is True


def test_lexical_presence_requires_dense_floor():
    # Lexical presence alone cannot rescue a very low dense score: content may
    # coincidentally mention a term without actually answering the question.
    policy = AnswerPolicy()  # presence floor 0.45
    evidence = [
        {"dense_score": 0.2, "lexical_score": 0.05},
    ]

    decision = policy.classify("ne renk?", evidence=evidence)

    assert decision.answerable is False


def test_no_lexical_presence_no_rescue():
    # No chunk contains the query's terms -> no lexical presence -> the strict
    # dense-only gate still applies and a modest dense score is rejected.
    policy = AnswerPolicy()
    evidence = [{"dense_score": 0.54}]  # below 0.55, and below presence floor anyway

    decision = policy.classify("what is the meaning of life?", evidence=evidence)

    assert decision.answerable is False
    assert decision.inputs["has_lexical_presence"] is False


def test_exact_identifier_rescues_low_dense_score():
    policy = AnswerPolicy()
    evidence = [{"dense_score": 0.1, "exact_identifier": True}]

    decision = policy.classify("UC-6 işletmeci numara yönetimi", evidence=evidence)

    assert decision.answerable is True
    assert decision.intent == INTENT_DOCUMENT


def test_no_answer_when_evidence_insufficient():
    policy = AnswerPolicy()
    evidence = [{"dense_score": 0.2}]  # below threshold, no lexical/identifier

    decision = policy.classify("Lisans maliyeti ne kadardır?", evidence=evidence)

    assert decision.answerable is False
    assert decision.intent == INTENT_DOCUMENT
    assert "insufficient evidence" in decision.reason


def test_low_dense_no_evidence_count_is_no_answer():
    policy = AnswerPolicy()
    decision = policy.classify("yıllık bakım bedeli?", evidence=[_dense(0.2)])

    assert decision.answerable is False


def test_policy_uses_configurable_threshold_not_bare_constant():
    # Same low-dense evidence is no-answer at the strict default threshold...
    strict = AnswerPolicy(score_threshold=0.9)
    strict_decision = strict.classify("soru nedir?", evidence=[_dense(0.8)])
    assert strict_decision.answerable is False

    # ...but answerable with a lower, explicitly-configured threshold. This shows
    # the decision follows an injectable policy value, not a single fixed 0.55.
    lenient = AnswerPolicy(score_threshold=0.5)
    lenient_decision = lenient.classify("soru nedir?", evidence=[_dense(0.8)])
    assert lenient_decision.answerable is True


def test_policy_respects_min_evidence():
    # A single candidate below threshold but above the configured score does NOT
    # answer when min_evidence requires more than one piece of evidence.
    policy = AnswerPolicy(score_threshold=0.7, min_evidence=2)
    decision = policy.classify("soru?", evidence=[_dense(0.85)])

    assert decision.answerable is False


def test_evidence_signal_from_dict_and_object_equivalent():
    signal_dict = EvidenceSignal.from_raw({"dense_score": 0.6, "exact_identifier": True})
    signal_obj = EvidenceSignal.from_raw(signal_dict)

    assert signal_dict.exact_identifier is True
    assert signal_obj.dense_score == 0.6


def test_content_lexical_presence_rescues_split_acronym_near_threshold():
    # Comparison query phrased with split acronyms ("ttnet sis" / "tt sis").
    # The AND-based lexical retriever returns nothing (lexical_score stays
    # None) because content lexemes are the single "ttnetsis"/"ttsis", but the
    # content-verified lexical_presence signal confirms the term is present,
    # so a near-threshold dense prompt must not be rejected.
    policy = AnswerPolicy()  # presence floor 0.45
    evidence = [
        {"dense_score": 0.51, "lexical_presence": True},
    ]

    decision = policy.classify(
        "ttnet sis ve tt sis arasındaki farklar nelerdir", evidence=evidence
    )

    assert decision.answerable is True
    assert decision.intent == INTENT_DOCUMENT
    assert decision.inputs["has_lexical_presence"] is True


def test_content_lexical_presence_still_requires_dense_floor():
    # Content presence alone cannot rescue a very low dense score (content may
    # coincidentally contain the term without answering the question).
    policy = AnswerPolicy()  # presence floor 0.45
    evidence = [
        {"dense_score": 0.2, "lexical_presence": True},
    ]

    decision = policy.classify("ttnet sis nedir?", evidence=evidence)

    assert decision.answerable is False
    assert decision.inputs["has_lexical_presence"] is True


def test_evidence_signal_parses_lexical_presence():
    sig = EvidenceSignal.from_raw({"dense_score": 0.5, "lexical_presence": True})
    assert sig.lexical_presence is True
    assert EvidenceSignal.from_raw({"dense_score": 0.5}).lexical_presence is False


def test_scores_and_inputs_recorded_in_decision():
    policy = AnswerPolicy()
    decision = policy.classify("renk kodu nedir?", evidence=[_dense(0.8)])

    assert decision.scores["top_dense_score"] == 0.8
    assert decision.evidence_count == 1
