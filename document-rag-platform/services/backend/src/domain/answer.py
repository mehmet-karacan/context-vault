"""Validated, immutable generation contracts for evidence-grounded answers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError


class NoAnswerReason(StrEnum):
    SMALLTALK = "smalltalk"
    POLICY_REFUSAL = "policy_refusal"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_FAILURE = "provider_failure"
    PERMISSION_DENIED = "permission_denied"
    MALFORMED_RESPONSE = "malformed_response"


class AnswerValidationCode(StrEnum):
    """Bounded diagnostics for structured-answer validation failures."""

    ENVELOPE_SCHEMA = "answer_validation.envelope_schema"
    ANSWERABLE_REASON_PRESENT = "answer_validation.answerable_reason_present"
    ANSWERABLE_TEXT_EMPTY = "answer_validation.answerable_text_empty"
    ANSWERABLE_CLAIMS_EMPTY = "answer_validation.answerable_claims_empty"
    UNANSWERABLE_REASON_MISSING = "answer_validation.unanswerable_reason_missing"
    UNANSWERABLE_CITATIONS_PRESENT = "answer_validation.unanswerable_citations_present"
    CLAIM_SOURCE_LABELS_INVALID = "answer_validation.claim_source_labels_invalid"
    CLAIM_TEXT_NOT_IN_ANSWER = "answer_validation.claim_text_not_in_answer"
    USED_SOURCE_LABELS_MISMATCH = "answer_validation.used_source_labels_mismatch"


def _shape_error(code: AnswerValidationCode) -> PydanticCustomError:
    return PydanticCustomError(code.value, "structured answer shape is invalid")


class AnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_text: str = Field(min_length=1, max_length=8000)
    source_labels: tuple[str, ...] = Field(min_length=1)


class AnswerEnvelope(BaseModel):
    """The sole output accepted from the generation boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    answerable: bool
    no_answer_reason: NoAnswerReason | None = None
    answer_text: str = Field(max_length=32000)
    claims: tuple[AnswerClaim, ...] = ()
    used_source_labels: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()
    safety_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _coherent_shape(self) -> "AnswerEnvelope":
        if self.answerable:
            if self.no_answer_reason is not None:
                raise _shape_error(AnswerValidationCode.ANSWERABLE_REASON_PRESENT)
            if not self.answer_text.strip():
                raise _shape_error(AnswerValidationCode.ANSWERABLE_TEXT_EMPTY)
            if not self.claims:
                raise _shape_error(AnswerValidationCode.ANSWERABLE_CLAIMS_EMPTY)
        elif self.no_answer_reason is None:
            raise _shape_error(AnswerValidationCode.UNANSWERABLE_REASON_MISSING)
        return self

    @classmethod
    def json_schema_contract(cls) -> dict[str, Any]:
        return cls.model_json_schema()


class AnswerValidationError(ValueError):
    """Raised when a schema-valid envelope is not grounded in its bundle."""

    def __init__(self, code: AnswerValidationCode) -> None:
        self.code = code
        super().__init__("structured answer validation failed")


def validate_grounding(
    envelope: AnswerEnvelope,
    *,
    allowed_labels: set[str],
) -> AnswerEnvelope:
    """Validate dynamic labels and answer/claim consistency fail-closed."""

    if not envelope.answerable:
        if envelope.claims or envelope.used_source_labels:
            raise AnswerValidationError(
                AnswerValidationCode.UNANSWERABLE_CITATIONS_PRESENT
            )
        return envelope

    claimed: list[str] = []
    normalized_answer = " ".join(envelope.answer_text.casefold().split())
    for claim in envelope.claims:
        labels = tuple(dict.fromkeys(claim.source_labels))
        if not labels or not set(labels).issubset(allowed_labels):
            raise AnswerValidationError(
                AnswerValidationCode.CLAIM_SOURCE_LABELS_INVALID
            )
        normalized_claim = " ".join(claim.claim_text.casefold().split())
        if normalized_claim not in normalized_answer:
            raise AnswerValidationError(AnswerValidationCode.CLAIM_TEXT_NOT_IN_ANSWER)
        claimed.extend(label for label in labels if label not in claimed)
    used = list(dict.fromkeys(envelope.used_source_labels))
    if used != claimed or not set(used).issubset(allowed_labels):
        raise AnswerValidationError(AnswerValidationCode.USED_SOURCE_LABELS_MISMATCH)
    return envelope
