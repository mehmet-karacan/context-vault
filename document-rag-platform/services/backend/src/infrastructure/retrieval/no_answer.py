"""No-answer / intent separation (Aşama 5.6 - No-answer ve intent ayrımı).

Implements the three-way behaviour from AKTIF_GOREV.md 5.6:

    A. Small-talk / greeting  -> deterministic short rules.
    B. Document question WITH sufficient evidence   -> answerable=True.
    C. Document question WITHOUT sufficient evidence -> answerable=False.

Design decisions:

- Empty retrieval results are NEVER treated as automatic small-talk
  (AKTIF_GOREV.md 5.6: "retrieval boşsa belge sorusunu otomatik günlük sohbet
  sayma"). Small-talk is decided purely from the normalized query text, before
  any retrieval evidence is considered; an empty result on a document query
  yields ``intent=document, answerable=False``.
- There is NO single fixed global ``0.55`` threshold as the only decision
  mechanism. ``AnswerPolicy`` combines an evidence count, the top dense score
  and lexical/identifier evidence: an exact identifier or a strong lexical match
  can make a low-dense-score result answerable, so a good match is never dropped
  just because the dense score is low.
- Thresholds come from ``settings`` (NO_ANSWER_SCORE_THRESHOLD,
  LEXICAL_STRONG_SCORE, NO_ANSWER_MIN_EVIDENCE, SMALLTALK_MIN_CONTENT_LEN) and
  are injectable, so the policy is deterministic and unit-testable, and can be
  calibrated against the golden dataset.

Greeting detection is a pure function (``is_smalltalk``) over normalized text
with a small keyword/rule set. A note is left here that this is the replacement
point for a small intent model if short-rule accuracy proves insufficient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...config import settings

__all__ = [
    "INTENT_SMALLTALK",
    "INTENT_DOCUMENT",
    "is_smalltalk",
    "normalize_query",
    "Answerability",
    "AnswerPolicy",
]

INTENT_SMALLTALK = "smalltalk"
INTENT_DOCUMENT = "document"

# Deterministic greeting / social keywords (lower-cased, matched after
# normalization). Replacing this small rule-set with a tiny intent model is the
# intended upgrade path (5.6: "gerekirse küçük intent modeline geçiş noktası").
GREETING_KEYWORDS = {
    "merhaba",
    "selam",
    "hello",
    "hi",
    "hey",
    "nasılsın",
    "nasilsin",
    "how are you",
    "iyiyim",
    "teşekkür",
    "tesekkur",
    "thanks",
    "thank you",
    "günaydın",
    "gunaydin",
    "iyi günler",
    "iyi gunler",
    "iyi akşamlar",
    "iyi aksamlar",
    "iyi geceler",
    "mrb",
    "sa",
    "as",
    "naber",
}

# Gazette of terms that almost never appear in a real document question by
# themselves; used only to decide "this is social, not a document query".
_SOCIAL_ONLY_TERMS = {"nasıl", "nasil", "nerede", "kim", "ne", "what", "who", "where"}


def normalize_query(text: str) -> str:
    """Lower-cases, strips and collapses whitespace/punctuation."""
    if not text:
        return ""
    lowered = text.lower()
    lowered = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", lowered)
    reduced = re.sub(r"[^\w\sÇĞİÖŞÜçğıöşü]", " ", lowered)
    return re.sub(r"\s+", " ", reduced).strip()


def _informative_document_query(normalized: str, min_content_len: int) -> bool:
    """True if the query has enough content to be a document question.

    A query that is empty, or collapses to only a greeting/social token, is not
    informative. This is a *heuristic input* to small-talk detection — it never
    decides on its own and never touches retrieval emptiness.
    """
    if len(normalized) < min_content_len:
        return False
    tokens = set(t for t in normalized.split() if len(t) > 1)
    if not tokens:
        return False
    return not tokens.issubset(_SOCIAL_ONLY_TERMS)


def is_smalltalk(
    query: str,
    *,
    min_content_len: Optional[int] = None,
    greeting_keywords: Optional[set] = None,
) -> bool:
    """Pure, deterministic small-talk detection over normalized text.

    Decides purely from the query text — retrieval evidence is irrelevant here,
    so an empty retrieval can never auto-classify a question as small-talk.
    """
    if min_content_len is None:
        min_content_len = settings.SMALLTALK_MIN_CONTENT_LEN
    keywords = greeting_keywords if greeting_keywords is not None else GREETING_KEYWORDS

    normalized = normalize_query(query)
    if not normalized:
        return True  # empty query -> treat as social, not a document question

    keywords = greeting_keywords if greeting_keywords is not None else GREETING_KEYWORDS

    # Small-talk is driven by greeting/social keywords. Without any greeting
    # token the query is treated as a (possibly weak) document question, never an
    # automatic small-talk — an empty retrieval is irrelevant to this decision.
    if not any(kw in normalized for kw in keywords):
        return False

    # A greeting token present, but if the query is an informative document
    # question we still treat it as a document query (e.g. "selam, X nasıl
    # yapılır?"). Genuine short greetings like "selam" / "merhaba mrb" stay
    # small-talk.
    if _informative_document_query(normalized, min_content_len):
        return False
    return True


@dataclass(frozen=True)
class EvidenceSignal:
    """Normalized per-candidate evidence used by :class:`AnswerPolicy`."""

    dense_score: Optional[float] = None
    lexical_score: Optional[float] = None
    identifier: bool = False
    exact_identifier: bool = False

    @classmethod
    def from_raw(cls, raw: Any) -> "EvidenceSignal":
        if isinstance(raw, EvidenceSignal):
            return raw
        if isinstance(raw, dict):
            keys = dict(raw)
        else:
            keys = {k: getattr(raw, k) for k in (
                "dense_score", "lexical_score", "identifier", "exact_identifier",
            ) if hasattr(raw, k)}
        return cls(
            dense_score=_to_float(keys.get("dense_score")),
            lexical_score=_to_float(keys.get("lexical_score")),
            identifier=bool(keys.get("identifier", False)),
            exact_identifier=bool(keys.get("exact_identifier", False)),
        )


@dataclass(frozen=True)
class Answerability:
    """Decision object describing intent and answerability for a query."""

    intent: str  # INTENT_SMALLTALK | INTENT_DOCUMENT
    answerable: bool
    reason: str
    evidence_count: int = 0
    scores: Dict[str, Optional[float]] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)
    policy_version: str = "no-answer-v1"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "intent": self.intent,
            "answerable": self.answerable,
            "reason": self.reason,
            "evidence_count": self.evidence_count,
            "scores": dict(self.scores),
            "inputs": dict(self.inputs),
            "policy_version": self.policy_version,
        }


def _to_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class AnswerPolicy:
    """Combines dense score, lexical/identifier evidence and evidence count.

    Thresholds are injectable (defaulting to ``settings``); never a bare
    hardcoded constant. The decision is deterministic and unit-testable.
    """

    def __init__(
        self,
        *,
        score_threshold: Optional[float] = None,
        min_evidence: Optional[int] = None,
        lexical_strong_score: Optional[float] = None,
        smalltalk_min_content_len: Optional[int] = None,
        lexical_presence_dense_floor: Optional[float] = None,
    ):
        self.score_threshold = (
            float(score_threshold)
            if score_threshold is not None
            else settings.NO_ANSWER_SCORE_THRESHOLD
        )
        self.min_evidence = (
            int(min_evidence)
            if min_evidence is not None
            else settings.NO_ANSWER_MIN_EVIDENCE
        )
        self.lexical_strong_score = (
            float(lexical_strong_score)
            if lexical_strong_score is not None
            else settings.LEXICAL_STRONG_SCORE
        )
        self.smalltalk_min_content_len = (
            int(smalltalk_min_content_len)
            if smalltalk_min_content_len is not None
            else settings.SMALLTALK_MIN_CONTENT_LEN
        )
        self.lexical_presence_dense_floor = (
            float(lexical_presence_dense_floor)
            if lexical_presence_dense_floor is not None
            else settings.LEXICAL_PRESENCE_DENSE_FLOOR
        )

    def classify(
        self,
        query: str,
        evidence: Optional[List[Any]] = None,
    ) -> Answerability:
        """Classifies ``query`` given retrieval ``evidence`` (list of dicts or
        EvidenceSignal-compatible objects)."""
        signals = [EvidenceSignal.from_raw(e) for e in (evidence or [])]

        if is_smalltalk(query, min_content_len=self.smalltalk_min_content_len):
            return Answerability(
                intent=INTENT_SMALLTALK,
                answerable=False,
                reason="deterministic small-talk/greeting rule",
                evidence_count=len(signals),
                scores=self._scores(signals),
                inputs={"normalized": normalize_query(query)},
            )

        dense = [s.dense_score for s in signals if s.dense_score is not None]
        top_dense = max(dense) if dense else None
        has_strong_lexical = any(
            s.lexical_score is not None and s.lexical_score >= self.lexical_strong_score
            for s in signals
        )
        # A lexical term-presence match: the query's significant term actually
        # appears in at least one retrieved chunk's content (search vector).
        # ts_rank_cd of a short acronym inside a large chunk is inherently tiny,
        # so this uses presence, not the "strong" ts_rank magnitude, to rescue a
        # near-threshold dense result whose content genuinely contains the term.
        has_lexical_presence = any(
            s.lexical_score is not None for s in signals
        )
        has_exact_identifier = any(s.exact_identifier for s in signals)
        has_identifier = any(s.identifier for s in signals)
        evidence_count = len(signals)
        base_inputs = {
            "normalized": normalize_query(query),
            "top_dense_score": top_dense,
            "has_strong_lexical": has_strong_lexical,
            "has_lexical_presence": has_lexical_presence,
            "has_exact_identifier": has_exact_identifier,
            "has_identifier": has_identifier,
        }
        scores = self._scores(signals)

        if evidence_count == 0:
            return Answerability(
                intent=INTENT_DOCUMENT,
                answerable=False,
                reason="document question but no retrieval evidence",
                evidence_count=evidence_count,
                scores=scores,
                inputs=base_inputs,
            )

        # Exact identifier / strong lexical evidence rescues a low dense score:
        # we never drop a result purely because its dense score is low.
        if has_exact_identifier or has_strong_lexical:
            return Answerability(
                intent=INTENT_DOCUMENT,
                answerable=True,
                reason="lexical/identifier evidence overrides low dense score",
                evidence_count=evidence_count,
                scores=scores,
                inputs=base_inputs,
            )

        # Content-verified rescue: the query's significant term demonstrably
        # occurs in the retrieved evidence (lexical term presence) and the top
        # dense score is at least a modest relevance floor. This admits a genuine
        # cross-lingual / short-acronym near-threshold match whose dense score
        # sits just under NO_ANSWER_SCORE_THRESHOLD, without fabricating: the
        # term really is in the source text. A no-answer query whose terms never
        # appear in any chunk has no lexical presence and is not rescued.
        if (
            has_lexical_presence
            and top_dense is not None
            and top_dense >= self.lexical_presence_dense_floor
        ):
            return Answerability(
                intent=INTENT_DOCUMENT,
                answerable=True,
                reason="lexical term presence in retrieved evidence confirms answer",
                evidence_count=evidence_count,
                scores=scores,
                inputs=base_inputs,
            )

        if evidence_count >= self.min_evidence and top_dense is not None and top_dense >= self.score_threshold:
            return Answerability(
                intent=INTENT_DOCUMENT,
                answerable=True,
                reason="sufficient dense evidence above configured threshold",
                evidence_count=evidence_count,
                scores=scores,
                inputs=base_inputs,
            )

        return Answerability(
            intent=INTENT_DOCUMENT,
            answerable=False,
            reason="insufficient evidence for answerable document question",
            evidence_count=evidence_count,
            scores=scores,
            inputs=base_inputs,
        )

    @staticmethod
    def _scores(signals: List[EvidenceSignal]) -> Dict[str, Optional[float]]:
        dense = [s.dense_score for s in signals if s.dense_score is not None]
        lexical = [s.lexical_score for s in signals if s.lexical_score is not None]
        return {
            "top_dense_score": max(dense) if dense else None,
            "max_lexical_score": max(lexical) if lexical else None,
        }
