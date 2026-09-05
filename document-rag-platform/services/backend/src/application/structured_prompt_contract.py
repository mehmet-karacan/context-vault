"""Canonical local structured-generation prompt serialization."""

from __future__ import annotations

import json
from typing import Any, Mapping


SCHEMA_INSTRUCTION = "Return exactly one JSON object matching this schema; no prose:"
INTERNAL_REPAIR_INSTRUCTION = (
    "The previous response failed validation. Re-evaluate the original request "
    "and return a fresh valid JSON object."
)


def serialize_schema(schema: Mapping[str, Any]) -> str:
    """Return the exact deterministic schema representation sent to the model."""

    return json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def local_structured_prompts(
    system: str,
    user: str,
    schema: Mapping[str, Any],
    *,
    internal_repair: bool = False,
) -> tuple[str, str]:
    """Build the exact LocalQwen structured request pair."""

    structured_system = f"{system}\n\n{SCHEMA_INSTRUCTION}\n{serialize_schema(schema)}"
    if internal_repair:
        structured_system = f"{structured_system}\n\n{INTERNAL_REPAIR_INSTRUCTION}"
    return structured_system, user
