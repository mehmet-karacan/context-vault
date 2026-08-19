"""Aşama 3.1: verifies the ``ParserRouter``.

Covers parser selection by extension / MIME / magic bytes, the "do not trust
the file blindly on extension-vs-MIME conflict" rule, unsupported-type errors,
and the per-file timeout + max-output-size enforcement.
"""

import time
from pathlib import Path

import pytest

from src.domain.normalized_content import NormalizedSource, UnitType
from src.infrastructure.parsers.router import (
    AmbiguousSourceTypeError,
    ParserOutputLimitError,
    ParserRouter,
    ParserTimeoutError,
    PlainTextParser,
    UnsupportedFileTypeError,
)


def _write(path: Path, data: bytes):
    path.write_bytes(data)
    return str(path)


# --- selection -------------------------------------------------------------


def test_router_selects_parser_by_extension():
    router = ParserRouter()
    assert router.detect_source_type(filename="doc.pdf") == "pdf"
    assert router.detect_source_type(filename="notes.txt") == "plain_text"
    assert router.detect_source_type(filename="readme.md") == "markdown"
    assert router.detect_source_type(filename="doc.docx") == "docx"


def test_router_selects_parser_by_mime_type():
    router = ParserRouter()
    assert router.detect_source_type(mime_type="application/pdf") == "pdf"
    assert router.detect_source_type(mime_type="text/plain") == "plain_text"
    assert (
        router.detect_source_type(
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        == "docx"
    )
    assert router.detect_source_type(mime_type="image/png") == "image"


def test_magic_bytes_override_claimed_extension_and_mime(tmp_path):
    # Actual content is a PDF, but it is named .txt and labelled text/plain.
    # The router must trust the detected actual type, not the client's claims.
    from pdf_fixtures import build_pdf

    path = _write(tmp_path / "report.txt", build_pdf(["A small but valid PDF page."]))
    router = ParserRouter()
    assert (
        router.detect_source_type(
            filename="report.txt", mime_type="text/plain", file_path=path
        )
        == "pdf"
    )
    result = router.parse(path, "report.txt", mime_type="text/plain")
    assert result.source_type == "document"


def test_plain_text_parser_produces_lined_paragraph_units(tmp_path):
    path = _write(tmp_path / "notes.txt", b"first line\n\nsecond line\n")
    result = ParserRouter().parse(path, "notes.txt")
    assert result.source_type == "plain_text"
    texts = [u.text for u in result.units]
    assert texts == ["first line", "second line"]
    assert result.units[1].locator.line_start == 3


def test_extension_mime_conflict_without_magic_raises(tmp_path):
    # No magic bytes (no file read) and extension conflicts with MIME -> do
    # not blindly trust; refuse to guess.
    router = ParserRouter()
    with pytest.raises(AmbiguousSourceTypeError):
        router.detect_source_type(filename="photo.png", mime_type="application/pdf")


def test_unsupported_type_raises(tmp_path):
    path = _write(tmp_path / "foo.xyz", b"junk")
    router = ParserRouter()
    with pytest.raises(UnsupportedFileTypeError):
        router.parse(path, "foo.xyz")


def test_unsupported_type_when_no_parser_registered(tmp_path):
    path = _write(tmp_path / "data.cfg", b"key=value")
    router = ParserRouter(registry={"plain_text": PlainTextParser()})
    # .cfg is not in the default extension table -> unresolvable source type.
    with pytest.raises(UnsupportedFileTypeError):
        router.parse(path, "data.cfg")


# --- timeout / output limit ------------------------------------------------


class _SlowParser:
    source_type = "slow"

    def supports(self, mime_type, extension):
        return True

    def parse(self, file_path, filename, options=None):
        time.sleep(30)
        return NormalizedSource(source_id="slow", source_type="slow")


class _BigParser:
    source_type = "big"

    def supports(self, mime_type, extension):
        return True

    def parse(self, file_path, filename, options=None):
        from src.domain.normalized_content import ContentUnit

        return NormalizedSource(
            source_id="big",
            source_type="big",
            units=[
                ContentUnit(
                    unit_id="b",
                    unit_type=UnitType.PARAGRAPH,
                    text="x" * 10_000,
                    order=0,
                )
            ],
        )


def test_parser_timeout_enforced(tmp_path):
    path = _write(tmp_path / "doc.txt", b"content")
    router = ParserRouter(
        registry={"plain_text": _SlowParser()},
        timeout_seconds=0.1,
    )
    with pytest.raises(ParserTimeoutError):
        router.parse(path, "doc.txt")


def test_router_reuses_default_settings_for_timeout_and_limit():
    router = ParserRouter()
    assert router.timeout_seconds == 300.0
    assert router.max_output_chars == 20000000


def test_output_size_limit_enforced(tmp_path):
    path = _write(tmp_path / "doc.txt", b"content")
    router = ParserRouter(
        registry={"plain_text": _BigParser()},
        max_output_chars=100,
    )
    with pytest.raises(ParserOutputLimitError):
        router.parse(path, "doc.txt")


def test_output_within_limit_succeeds(tmp_path):
    path = _write(tmp_path / "notes.txt", b"hello world")
    router = ParserRouter(registry={"plain_text": PlainTextParser()})
    result = router.parse(path, "notes.txt")
    assert result.units[0].text == "hello world"
