from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest

from src.infrastructure.parsers.router import ParserRouter
from src.infrastructure.security.source_detection import describe_upload


CFG = SimpleNamespace(
    MAX_DOCUMENT_BYTES=100_000,
    MAX_TOTAL_INGESTION_BYTES=1_000_000,
    MIME_VALIDATION_STRICT=True,
)


def _docx() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
    return target.getvalue()


@pytest.mark.parametrize(
    "payload,expected_mime,expected_type",
    [
        (b"extensionless UTF-8 text\n", "text/plain", "plain_text"),
        (b"%PDF-1.4\n%%EOF\n", "application/pdf", "pdf"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/unknown", "image"),
        (
            _docx(),
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "docx",
        ),
    ],
)
def test_extensionless_source_uses_content_signals(
    tmp_path, payload: bytes, expected_mime: str, expected_type: str
) -> None:
    path = tmp_path / "source"
    path.write_bytes(payload)
    descriptor = describe_upload(
        payload,
        filename="source",
        declared_mime=None,
        classification="internal",
        config=CFG,
    )
    assert descriptor.detected_mime == expected_mime
    assert (
        ParserRouter().detect_source_type(filename="source", file_path=str(path))
        == expected_type
    )


def test_declared_and_detected_mime_difference_becomes_policy_event() -> None:
    descriptor = describe_upload(
        b"plain markdown-compatible text",
        filename="notes.md",
        declared_mime="text/markdown",
        classification="internal",
        config=CFG,
    )
    assert descriptor.detected_mime == "text/plain"
    assert descriptor.policy_events == ("declared_detected_mime_difference",)
