"""Aşama 3.4: verifies the plain-text and Markdown parsers.

Covers the ``PlainTextParser`` and ``MarkdownParser`` adapters against the
3.4 acceptance criteria:

- Encoding detection / controlled UTF-8 fallback for TXT (BOM-aware, common
  single-byte encodings, ``errors="replace"`` last resort).
- Markdown structure preservation: headings (with heading_path rebuilt from
  ``#``-levels), code fences, lists and tables mapped to the correct
  ``ContentUnit`` types.
- Line-number info (``SourceLocator.line_start`` / ``line_end``) for the
  plain-text parser, including large files.
- Every ``ContentUnit`` carries order, hierarchy, locator and metadata, and a
  ``NormalizedSource`` round-trips drop-free through JSON.

Static fixtures live in ``tests/fixtures/parsers/``; byte-level cases
(encoding paths) are generated inline via ``tmp_path``.
"""

import json
from pathlib import Path

import pytest

from src.domain.normalized_content import NormalizedSource, UnitType
from src.infrastructure.parsers.router import MarkdownParser, PlainTextParser

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "parsers"


def _parse(parser, fixture: str):
    path = FIXTURES / fixture
    return parser.parse(str(path), path.name)


# --- plain-text: encoding --------------------------------------------------


def test_plain_text_utf8_turkish_sample_blocks_and_offsets():
    result = _parse(PlainTextParser(), "plain/turkish_sample.txt")
    assert result.source_type == "plain_text"
    assert result.title == "turkish_sample.txt"
    assert result.metadata["encoding"] == "utf-8"
    assert [u.text for u in result.units] == [
        "Merhaba Dünya",
        "Bu bir Türkçe metindir.\nAkış ve çözümleme bağlamında bir örnek.",
        "Son satır.",
    ]
    assert [u.locator.line_start for u in result.units] == [1, 3, 6]
    assert [u.locator.line_end for u in result.units] == [1, 4, 6]


def test_plain_text_detects_cp1254_for_non_utf8_input():
    # The cp1254 fixture contains 0x80-0xFF bytes, so it is NOT valid UTF-8;
    # the parser must fall back to a single-byte encoding but still succeed.
    raw = (FIXTURES / "plain/cp1254_sample.txt").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    result = _parse(PlainTextParser(), "plain/cp1254_sample.txt")
    assert result.metadata["encoding"] == "cp1254"
    assert result.units[0].text.startswith("Merhaba dünya")
    assert "ğüşöç" in result.units[0].text


def test_plain_text_detects_utf16_bom(tmp_path):
    p = tmp_path / "le.txt"
    p.write_bytes(b"\xff\xfe" + "merhaba\n\nsatır".encode("utf-16-le"))
    result = PlainTextParser().parse(str(p), "le.txt")
    assert result.metadata["encoding"] == "utf-16-le"
    texts = [u.text for u in result.units]
    assert texts == ["merhaba", "satır"]


def test_plain_text_detects_utf8_sig_bom(tmp_path):
    p = tmp_path / "sig.txt"
    p.write_bytes(b"\xef\xbb\xbf" + "hello\n\nworld".encode("utf-8"))
    result = PlainTextParser().parse(str(p), "sig.txt")
    assert result.metadata["encoding"] == "utf-8-sig"
    assert [u.text for u in result.units] == ["hello", "world"]


def test_plain_text_single_byte_fallback_for_invalid_utf8(tmp_path):
    # Bytes that are not valid UTF-8 must still parse via the single-byte
    # encoding fallback; parsing must never raise.
    p = tmp_path / "garbage.txt"
    p.write_bytes(b"ok \xff\xfe\x80\nline2")
    result = PlainTextParser().parse(str(p), "garbage.txt")
    assert result.metadata["encoding"] == "cp1254"
    assert len(result.units) >= 1


# --- plain-text: line info -------------------------------------------------


def test_plain_text_large_file_line_offsets():
    result = _parse(PlainTextParser(), "plain/large_sample.txt")
    assert result.metadata["line_count"] == 151
    blocks = [(u.locator.line_start, u.locator.line_end) for u in result.units]
    assert blocks == [(1, 100), (102, 151)]
    assert result.units[0].text.startswith("Satır 1")
    assert result.units[1].text.startswith("İkinci blok")


def test_plain_text_units_carry_hierarchy_locator_metadata():
    result = _parse(PlainTextParser(), "plain/turkish_sample.txt")
    for u in result.units:
        assert u.unit_type == UnitType.PARAGRAPH
        assert u.hierarchy is not None
        assert u.hierarchy.heading_path == []
        assert u.locator is not None
        assert u.locator.file_path == "turkish_sample.txt"
        assert u.locator.block_index is not None
        assert u.metadata["encoding"] == "utf-8"
        assert u.order > 0


def test_plain_text_title_language_from_options():
    path = FIXTURES / "plain/turkish_sample.txt"
    result = PlainTextParser().parse(
        str(path), "turkish_sample.txt", options={"language": "tr"}
    )
    assert result.language == "tr"


def test_plain_text_round_trips_through_json():
    result = _parse(PlainTextParser(), "plain/turkish_sample.txt")
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    restored = NormalizedSource.from_dict(json.loads(payload))
    assert restored == result


# --- markdown: structure ---------------------------------------------------


def test_markdown_headings_preserve_heading_path():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    headings = [u for u in result.units if u.unit_type == UnitType.HEADING]
    assert [h.text for h in headings] == ["Proje Giriş", "Kurulum", "Veri", "Adımlar"]
    assert [h.hierarchy.depth for h in headings] == [1, 2, 2, 2]
    assert headings[0].hierarchy.heading_path == ["Proje Giriş"]
    assert headings[1].hierarchy.heading_path == ["Proje Giriş", "Kurulum"]
    assert headings[2].hierarchy.heading_path == ["Proje Giriş", "Veri"]


def test_markdown_code_fence_is_code_unit():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    code = [u for u in result.units if u.unit_type == UnitType.CODE]
    assert len(code) == 1
    unit = code[0]
    assert unit.markdown.startswith("```bash")
    assert unit.markdown.rstrip().endswith("```")
    assert "pip install contextvault" in unit.text
    assert unit.metadata["language"] == "bash"
    assert unit.hierarchy.heading_path == ["Proje Giriş", "Kurulum"]
    assert (unit.locator.line_start, unit.locator.line_end) == (8, 11)


def test_markdown_list_items():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    items = [u for u in result.units if u.unit_type == UnitType.LIST_ITEM]
    assert [i.text for i in items] == [
        "- birinci madde",
        "- ikinci madde",
        "1. numaralı madde",
    ]
    assert items[0].hierarchy.heading_path == ["Proje Giriş", "Kurulum"]
    assert [i.locator.line_start for i in items] == [13, 14, 15]


def test_markdown_table_is_table_unit():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    tables = [u for u in result.units if u.unit_type == UnitType.TABLE]
    assert len(tables) == 1
    table = tables[0]
    assert "Katman" in table.markdown and "Sorumluluk" in table.markdown
    assert table.metadata["row_count"] == 4
    assert table.hierarchy.heading_path == ["Proje Giriş", "Veri"]
    assert (table.locator.line_start, table.locator.line_end) == (19, 22)


def test_markdown_paragraphs():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    paras = [u for u in result.units if u.unit_type == UnitType.PARAGRAPH]
    assert any("Holistik özet" in p.text for p in paras)
    assert any(p.text == "Sonuç paragrafı." for p in paras)
    assert paras[-1].hierarchy.heading_path == ["Proje Giriş", "Adımlar"]


def test_markdown_round_trips_through_json():
    result = _parse(MarkdownParser(), "markdown/sample.md")
    payload = json.dumps(result.to_dict(), ensure_ascii=False)
    restored = NormalizedSource.from_dict(json.loads(payload))
    assert restored == result


def test_markdown_encoding_fallback(tmp_path):
    p = tmp_path / "bad.md"
    p.write_bytes(b"# Baslik \xff\xfe\n")
    result = MarkdownParser().parse(str(p), "bad.md")
    assert result.units[0].unit_type == UnitType.HEADING


def test_markdown_multi_hyphen_separator(tmp_path):
    p = tmp_path / "t.md"
    p.write_text(
        "| A | B |\n|----|:---:|\n| 1 | 2 |\n", encoding="utf-8"
    )
    result = MarkdownParser().parse(str(p), "t.md")
    tables = [u for u in result.units if u.unit_type == UnitType.TABLE]
    assert len(tables) == 1
    assert tables[0].metadata["row_count"] == 3


# --- supports() slots -------------------------------------------------------


def test_plain_text_supports_txt_slots():
    parser = PlainTextParser()
    assert parser.supports("text/plain", ".txt")
    assert parser.supports("", ".text")
    assert not parser.supports("text/markdown", ".md")
    assert not parser.supports("application/pdf", ".pdf")


def test_markdown_supports_md_slots():
    parser = MarkdownParser()
    assert parser.supports("text/markdown", ".md")
    assert parser.supports("text/x-markdown", ".markdown")
    assert not parser.supports("text/plain", ".txt")
