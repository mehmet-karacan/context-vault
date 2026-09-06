"""Canonical parser and chunker adapters shared by every ingestion source."""

from __future__ import annotations

from typing import Optional

from .ingestion_bundle import BUNDLE_MIME, parse_scan_bundle
from ..domain.normalized_content import NormalizedSource
from ..infrastructure.chunkers.registry import ChunkerRegistry
from ..infrastructure.parsers.router import ParserRouter


def parse_source(
    file_path: str, filename: str, mime_type: Optional[str] = None
) -> NormalizedSource:
    if mime_type == BUNDLE_MIME:
        with open(file_path, "rb") as handle:
            return parse_scan_bundle(handle.read())
    return ParserRouter().parse(file_path, filename, mime_type=mime_type)


def chunk_source(source: NormalizedSource, **_ignored_legacy_options) -> list:
    return ChunkerRegistry().chunk(source)
