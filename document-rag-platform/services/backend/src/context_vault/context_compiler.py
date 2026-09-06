"""Deterministic, bounded and provider-aware context compilation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable


class PolicyViolation(ValueError):
    """A compilation request violates an explicit context policy."""


class BudgetExceeded(ValueError):
    """Pinned context cannot fit without violating the model budget."""


class LoadTier(StrEnum):
    MUST_LOAD = "MUST_LOAD"
    SHOULD_LOAD_IF_RELEVANT = "SHOULD_LOAD_IF_RELEVANT"
    RETRIEVE_ON_DEMAND = "RETRIEVE_ON_DEMAND"
    NEVER_AUTO_LOAD = "NEVER_AUTO_LOAD"


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class ItemState(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    STALE = "STALE"


class ContextRole(StrEnum):
    SECURITY_POLICY = "SECURITY_POLICY"
    ACTIVE_WORK = "ACTIVE_WORK"
    PROJECT = "PROJECT"
    KNOWLEDGE = "KNOWLEDGE"
    RECEIPT = "RECEIPT"
    RETRIEVAL = "RETRIEVAL"


@dataclass(frozen=True)
class ProviderContextPolicy:
    provider_id: str
    is_remote: bool
    allowed_classifications: frozenset[DataClassification]

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or not self.provider_id.strip():
            raise PolicyViolation("provider_id cannot be empty")
        if not isinstance(self.is_remote, bool):
            raise PolicyViolation("is_remote must be an exact boolean")
        if isinstance(self.allowed_classifications, (str, bytes)):
            raise PolicyViolation("allowed classification collection is invalid")
        try:
            normalized = frozenset(self.allowed_classifications)
        except TypeError as exc:
            raise PolicyViolation(
                "allowed classification collection is invalid"
            ) from exc
        if not normalized or any(
            not isinstance(classification, DataClassification)
            for classification in normalized
        ):
            raise PolicyViolation(
                "provider must allow at least one exact data classification enum"
            )
        object.__setattr__(self, "allowed_classifications", normalized)


@dataclass(frozen=True)
class ContextItem:
    source_id: str
    logical_id: str
    version: int
    content: str
    content_hash: str
    reason: str
    token_cost: int
    load_tier: LoadTier
    classification: DataClassification
    role: ContextRole = ContextRole.KNOWLEDGE
    state: ItemState = ItemState.ACTIVE
    relevance_score: float = 0.0
    supersedes: tuple[str, ...] = ()
    local_only: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.source_id, str)
            or not isinstance(self.logical_id, str)
            or not self.source_id.strip()
            or not self.logical_id.strip()
        ):
            raise PolicyViolation("source_id and logical_id cannot be empty")
        if self.version <= 0:
            raise PolicyViolation("context item version must be positive")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise PolicyViolation("context item inclusion reason cannot be empty")
        if not isinstance(self.content, str):
            raise PolicyViolation("context item content must be a string")
        if (
            isinstance(self.token_cost, bool)
            or not isinstance(self.token_cost, int)
            or self.token_cost != verified_token_cost(self.content)
        ):
            raise PolicyViolation(
                "context item token_cost must equal the deterministic UTF-8 byte cost"
            )
        if not 0.0 <= self.relevance_score <= 1.0:
            raise PolicyViolation("relevance_score must be between 0 and 1")
        if _content_hash(self.content) != self.content_hash:
            raise PolicyViolation("context item content_hash does not match content")
        if self.role in {ContextRole.SECURITY_POLICY, ContextRole.ACTIVE_WORK} and (
            self.load_tier is not LoadTier.MUST_LOAD
        ):
            raise PolicyViolation("security policy and active work must use MUST_LOAD")

    @classmethod
    def from_content(
        cls,
        *,
        source_id: str,
        logical_id: str,
        version: int,
        content: str,
        reason: str,
        token_cost: int,
        load_tier: LoadTier,
        classification: DataClassification,
        role: ContextRole = ContextRole.KNOWLEDGE,
        state: ItemState = ItemState.ACTIVE,
        relevance_score: float = 0.0,
        supersedes: tuple[str, ...] = (),
        local_only: bool = False,
    ) -> ContextItem:
        return cls(
            source_id=source_id,
            logical_id=logical_id,
            version=version,
            content=content,
            content_hash=_content_hash(content),
            reason=reason,
            token_cost=token_cost,
            load_tier=load_tier,
            classification=classification,
            role=role,
            state=state,
            relevance_score=relevance_score,
            supersedes=supersedes,
            local_only=local_only,
        )


@dataclass(frozen=True)
class ExcludedItem:
    source_id: str
    reason: str


@dataclass(frozen=True)
class CompiledContext:
    schema_version: int
    attempt_id: str
    provider_id: str
    items: tuple[ContextItem, ...]
    excluded: tuple[ExcludedItem, ...]
    token_cost: int
    available_input_tokens: int
    manifest_hash: str
    _canonical_payload_json: str = field(repr=False)

    @property
    def core_text(self) -> str:
        """Stable provider-neutral rendering; adapters may wrap but not alter it."""

        return "\n\n".join(item.content for item in self.items)

    def canonical_payload(self) -> dict[str, Any]:
        """Return a copy-safe payload whose canonical SHA-256 is ``manifest_hash``."""

        return json.loads(self._canonical_payload_json)


class ContextCompiler:
    """Compile a semantic core without preloading the vault."""

    def compile(
        self,
        *,
        attempt_id: str,
        items: Iterable[ContextItem],
        provider_policy: ProviderContextPolicy,
        context_window: int,
        reserved_output: int,
        safety_margin: int,
        relevant_source_ids: frozenset[str] = frozenset(),
        retrieved_source_ids: frozenset[str] = frozenset(),
        preload_all: bool = False,
    ) -> CompiledContext:
        if not attempt_id.strip():
            raise PolicyViolation("attempt_id is required to bind compiled context")
        if not isinstance(preload_all, bool):
            raise PolicyViolation("preload_all must be an exact boolean")
        if preload_all:
            raise PolicyViolation("full-vault preload is forbidden")
        if not _valid_budget_integer(context_window, positive=True) or not all(
            _valid_budget_integer(value, positive=False)
            for value in (reserved_output, safety_margin)
        ):
            raise PolicyViolation("token budget values are invalid")
        available = context_window - reserved_output - safety_margin
        if available <= 0:
            raise BudgetExceeded(
                "reserved output and safety margin exhaust context window"
            )

        candidates, excluded = self._eligible_items(
            tuple(items),
            provider_policy=provider_policy,
            relevant_source_ids=relevant_source_ids,
            retrieved_source_ids=retrieved_source_ids,
        )
        mandatory = tuple(
            item for item in candidates if item.load_tier is LoadTier.MUST_LOAD
        )
        mandatory_cost = sum(item.token_cost for item in mandatory)
        if mandatory_cost > available:
            raise BudgetExceeded(
                "security policy, active work, or other MUST_LOAD context exceeds input budget"
            )

        selected = list(mandatory)
        remaining = available - mandatory_cost
        optional = sorted(
            (item for item in candidates if item.load_tier is not LoadTier.MUST_LOAD),
            key=lambda item: (
                0 if item.load_tier is LoadTier.SHOULD_LOAD_IF_RELEVANT else 1,
                -item.relevance_score,
                item.logical_id,
                -item.version,
                item.source_id,
            ),
        )
        for item in optional:
            if item.token_cost <= remaining:
                selected.append(item)
                remaining -= item.token_cost
            else:
                excluded.append(ExcludedItem(item.source_id, "token_budget"))

        ordered = tuple(sorted(selected, key=_context_order))
        ordered_excluded = tuple(
            sorted(excluded, key=lambda item: (item.source_id, item.reason))
        )
        token_cost = sum(item.token_cost for item in ordered)
        manifest_payload = {
            "schema_version": 1,
            "attempt_id": attempt_id,
            "provider": {
                "provider_id": provider_policy.provider_id,
                "is_remote": provider_policy.is_remote,
                "allowed_classifications": sorted(
                    provider_policy.allowed_classifications
                ),
            },
            "budget": {
                "context_window": context_window,
                "reserved_output": reserved_output,
                "safety_margin": safety_margin,
                "available_input_tokens": available,
                "token_cost": token_cost,
            },
            "items": [_item_manifest(item) for item in ordered],
            # Excluded source identifiers can themselves disclose sensitive paths.
            # The provider-bound payload records only aggregate policy reasons;
            # the local CompiledContext retains the detailed audit tuple.
            "exclusion_summary": _exclusion_summary(ordered_excluded),
        }
        canonical_payload_json = _canonical_json(manifest_payload)
        return CompiledContext(
            schema_version=1,
            attempt_id=attempt_id,
            provider_id=provider_policy.provider_id,
            items=ordered,
            excluded=ordered_excluded,
            token_cost=token_cost,
            available_input_tokens=available,
            manifest_hash=hashlib.sha256(canonical_payload_json.encode()).hexdigest(),
            _canonical_payload_json=canonical_payload_json,
        )

    def _eligible_items(
        self,
        items: tuple[ContextItem, ...],
        *,
        provider_policy: ProviderContextPolicy,
        relevant_source_ids: frozenset[str],
        retrieved_source_ids: frozenset[str],
    ) -> tuple[tuple[ContextItem, ...], list[ExcludedItem]]:
        source_ids = [item.source_id for item in items]
        if len(source_ids) != len(set(source_ids)):
            raise PolicyViolation("source_id must be unique within a compile request")

        critical_roles = {ContextRole.SECURITY_POLICY, ContextRole.ACTIVE_WORK}
        critical_items = tuple(item for item in items if item.role in critical_roles)
        if any(item.state is not ItemState.ACTIVE for item in critical_items):
            raise PolicyViolation(
                "critical MUST_LOAD context cannot be stale or superseded"
            )
        logical_counts: dict[str, int] = {}
        for item in items:
            logical_counts[item.logical_id] = logical_counts.get(item.logical_id, 0) + 1
        if any(logical_counts[item.logical_id] > 1 for item in critical_items):
            raise PolicyViolation("ambiguous critical MUST_LOAD revisions")

        superseded_ids = {
            source_id
            for item in items
            if item.state is ItemState.ACTIVE
            for source_id in item.supersedes
        }
        if any(item.source_id in superseded_ids for item in critical_items):
            raise PolicyViolation("critical MUST_LOAD context cannot be superseded")
        exclusions: list[ExcludedItem] = []
        eligible: list[ContextItem] = []
        for item in sorted(items, key=lambda value: value.source_id):
            reason = self._exclusion_reason(
                item,
                provider_policy=provider_policy,
                relevant_source_ids=relevant_source_ids,
                retrieved_source_ids=retrieved_source_ids,
                superseded_ids=superseded_ids,
            )
            if reason:
                if item.load_tier is LoadTier.MUST_LOAD and reason in {
                    "provider_classification_policy",
                    "local_only",
                }:
                    raise PolicyViolation(
                        f"MUST_LOAD item {item.source_id!r} is blocked by {reason}"
                    )
                exclusions.append(ExcludedItem(item.source_id, reason))
            else:
                eligible.append(item)

        # Multiple active revisions for one logical source collapse deterministically.
        winner_by_logical: dict[str, ContextItem] = {}
        for item in eligible:
            current = winner_by_logical.get(item.logical_id)
            if current is None or (item.version, item.content_hash, item.source_id) > (
                current.version,
                current.content_hash,
                current.source_id,
            ):
                if current is not None:
                    exclusions.append(
                        ExcludedItem(current.source_id, "duplicate_or_older_revision")
                    )
                winner_by_logical[item.logical_id] = item
            else:
                exclusions.append(
                    ExcludedItem(item.source_id, "duplicate_or_older_revision")
                )
        return tuple(winner_by_logical.values()), exclusions

    @staticmethod
    def _exclusion_reason(
        item: ContextItem,
        *,
        provider_policy: ProviderContextPolicy,
        relevant_source_ids: frozenset[str],
        retrieved_source_ids: frozenset[str],
        superseded_ids: set[str],
    ) -> str | None:
        if item.load_tier is LoadTier.NEVER_AUTO_LOAD:
            return "never_auto_load"
        if item.state is ItemState.SUPERSEDED or item.source_id in superseded_ids:
            return "superseded"
        if item.state is ItemState.STALE:
            return "stale"
        if item.classification not in provider_policy.allowed_classifications:
            return "provider_classification_policy"
        if provider_policy.is_remote and item.local_only:
            return "local_only"
        if item.load_tier is LoadTier.SHOULD_LOAD_IF_RELEVANT and (
            item.source_id not in relevant_source_ids and item.relevance_score == 0
        ):
            return "not_relevant"
        if item.load_tier is LoadTier.RETRIEVE_ON_DEMAND and (
            item.source_id not in retrieved_source_ids
        ):
            return "not_retrieved"
        return None


def _context_order(item: ContextItem) -> tuple[int, str, int, str]:
    role_order = {
        ContextRole.SECURITY_POLICY: 0,
        ContextRole.ACTIVE_WORK: 1,
        ContextRole.PROJECT: 2,
        ContextRole.KNOWLEDGE: 3,
        ContextRole.RECEIPT: 4,
        ContextRole.RETRIEVAL: 5,
    }
    return role_order[item.role], item.logical_id, -item.version, item.source_id


def _item_manifest(item: ContextItem) -> dict[str, object]:
    return {
        "source_id": item.source_id,
        "logical_id": item.logical_id,
        "version": item.version,
        "hash": item.content_hash,
        "reason": item.reason,
        "token_cost": item.token_cost,
        "load_tier": item.load_tier,
        "classification": item.classification,
        "role": item.role,
        "content": item.content,
    }


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def verified_token_cost(content: str) -> int:
    """Return a conservative deterministic input-cost bound.

    UTF-8 byte length upper-bounds byte-level tokenizer output and does not
    trust a caller/model supplied estimate.
    """

    cost = len(content.encode("utf-8"))
    if cost <= 0:
        raise PolicyViolation("context item content cannot be empty")
    return cost


def _exclusion_summary(items: tuple[ExcludedItem, ...]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for item in items:
        summary[item.reason] = summary.get(item.reason, 0) + 1
    return dict(sorted(summary.items()))


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _valid_budget_integer(value: object, *, positive: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return value > 0 if positive else value >= 0
