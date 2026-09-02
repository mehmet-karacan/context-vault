"""Typed source and content-policy contracts for the canonical ingestion path."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple


POLICY_VERSION = "cv-content-policy-v1"


@dataclass(frozen=True)
class SourceDescriptor:
    source_type: str
    origin: str
    revision: Optional[str]
    content_length: int
    content_hash: str
    detected_mime: str
    declared_mime: Optional[str]
    data_classification_hint: str
    policy_events: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ContentPolicyDecision:
    classification: str
    contains_credentials: bool
    contains_private_key: bool
    contains_pii: bool
    permit_original_storage: bool
    permit_normalized_storage: bool
    permit_local_embedding: bool
    permit_remote_embedding: bool
    permit_local_generation: bool
    permit_remote_generation: bool
    redaction_required: bool
    quarantine_reason: Optional[str]
    policy_version: str = POLICY_VERSION

    @property
    def quarantined(self) -> bool:
        return self.quarantine_reason is not None


_PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE
)
_CREDENTIALS = (
    re.compile(r"\b(?:api[_-]?key|secret|token|password)\s*[:=]\s*[^\s]{8,}", re.I),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
)
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_TURKISH_ID = re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)")


def source_fingerprint(descriptor: SourceDescriptor) -> str:
    material = "\x1f".join(
        (
            descriptor.source_type,
            descriptor.origin,
            descriptor.revision or "",
            str(descriptor.content_length),
            descriptor.content_hash,
            descriptor.detected_mime,
            descriptor.declared_mime or "",
            descriptor.data_classification_hint,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def decide_content_policy(
    content: bytes,
    *,
    classification: str = "internal",
) -> ContentPolicyDecision:
    """Classify without returning or logging matching secret values."""
    normalized_classification = classification.strip().lower()
    if normalized_classification not in {
        "public",
        "internal",
        "confidential",
        "restricted",
    }:
        raise ValueError("unsupported data classification")

    probe = content.decode("utf-8", errors="replace")
    contains_private_key = bool(_PRIVATE_KEY.search(probe))
    contains_credentials = any(pattern.search(probe) for pattern in _CREDENTIALS)
    contains_pii = bool(_EMAIL.search(probe) or _TURKISH_ID.search(probe))
    high_risk = contains_private_key or contains_credentials
    remote_allowed = normalized_classification in {"public", "internal"} and not (
        high_risk or contains_pii
    )

    reason = None
    if contains_private_key:
        reason = "private_key_detected"
    elif contains_credentials:
        reason = "credential_detected"

    return ContentPolicyDecision(
        classification=normalized_classification,
        contains_credentials=contains_credentials,
        contains_private_key=contains_private_key,
        contains_pii=contains_pii,
        permit_original_storage=not high_risk,
        permit_normalized_storage=not high_risk,
        permit_local_embedding=not high_risk,
        permit_remote_embedding=remote_allowed,
        permit_local_generation=not high_risk,
        permit_remote_generation=remote_allowed,
        redaction_required=contains_pii,
        quarantine_reason=reason,
    )
