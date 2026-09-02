"""Validated, immutable generation contracts for evidence-grounded answers."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NoAnswerReason(StrEnum):
    SMALLTALK = "smalltalk"
    POLICY_REFUSAL = "policy_refusal"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_FAILURE = "provider_failure"
    PERMISSION_DENIED = "permission_denied"
    MALFORMED_RESPONSE = "malformed_response"


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
                raise ValueError("answerable response cannot have no_answer_reason")
            if not self.answer_text.strip() or not self.claims:
                raise ValueError("answerable response requires text and claims")
        elif self.no_answer_reason is None:
            raise ValueError("unanswerable response requires no_answer_reason")
        return self

    @classmethod
    def json_schema_contract(cls) -> dict[str, Any]:
        return cls.model_json_schema()


class AnswerValidationError(ValueError):
    """Raised when a schema-valid envelope is not grounded in its bundle."""


def validate_grounding(
    envelope: AnswerEnvelope,
    *,
    allowed_labels: set[str],
) -> AnswerEnvelope:
    """Validate dynamic labels and answer/claim consistency fail-closed."""

    if not envelope.answerable:
        if envelope.claims or envelope.used_source_labels:
            raise AnswerValidationError("no-answer response cannot cite evidence")
        return envelope

    claimed: list[str] = []
    normalized_answer = " ".join(envelope.answer_text.casefold().split())
    for claim in envelope.claims:
        labels = tuple(dict.fromkeys(claim.source_labels))
        if not labels or not set(labels).issubset(allowed_labels):
            raise AnswerValidationError("claim contains unknown or empty source labels")
        normalized_claim = " ".join(claim.claim_text.casefold().split())
        if normalized_claim not in normalized_answer:
            raise AnswerValidationError("claim text is not present in answer text")
        claimed.extend(label for label in labels if label not in claimed)
    used = list(dict.fromkeys(envelope.used_source_labels))
    if used != claimed or not set(used).issubset(allowed_labels):
        raise AnswerValidationError("used_source_labels do not match grounded claims")
    return envelope
