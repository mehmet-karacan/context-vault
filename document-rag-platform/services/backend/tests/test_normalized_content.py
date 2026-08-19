"""Aşama 3: verifies the normalized content model (Bölüm 6).

Covers the exhaustive ``unit_type`` set, dataclass field shapes, and lossless
JSON round-tripping of ``NormalizedSource`` (the design requires normalized
output to be storable as JSON — Section 6).
"""

import json

import pytest

from src.domain.normalized_content import (
    UNIT_TYPES,
    ContentUnit,
    Hierarchy,
    NormalizedSource,
    SourceLocator,
    UnitType,
)


def _sample_source() -> NormalizedSource:
    return NormalizedSource(
        source_id="src-1",
        version_id="ver-1",
        source_type="markdown",
        title="Sample",
        language="tr",
        metadata={"kind": "test", "nested": {"a": 1}},
        units=[
            ContentUnit(
                unit_id="u1",
                unit_type=UnitType.HEADING,
                text="Başlık",
                markdown="# Başlık",
                order=1,
                hierarchy=Hierarchy(heading_path=["Başlık"], depth=1),
                locator=SourceLocator(
                    page_start=1,
                    page_end=1,
                    bbox=[0.0, 0.0, 100.0, 20.0],
                    file_path="doc.md",
                    line_start=1,
                    line_end=1,
                ),
                metadata={"style": "Heading 1"},
            ),
            ContentUnit(
                unit_id="u2",
                unit_type=UnitType.PARAGRAPH,
                text="paragraf",
                markdown="paragraf",
                order=2,
                hierarchy=Hierarchy(
                    heading_path=["Başlık"], parent_unit_id="u1", depth=2
                ),
                locator=SourceLocator(
                    file_path="doc.md", line_start=3, line_end=4, block_index=2
                ),
            ),
            ContentUnit(
                unit_id="u3",
                unit_type=UnitType.TABLE,
                text="a",
                order=3,
            ),
        ],
    )


def test_unit_type_enum_is_exhaustive_and_unique():
    expected = [
        "heading",
        "paragraph",
        "list_item",
        "table",
        "code",
        "formula",
        "image",
        "image_caption",
        "ocr_text",
        "page_break",
        "file_header",
        "symbol",
        "configuration",
    ]
    values = [member.value for member in UnitType]
    assert values == expected
    assert len(set(values)) == len(values) == 13
    assert UNIT_TYPES == tuple(expected)


def test_normalized_source_round_trips_through_json_losslessly():
    original = _sample_source()
    payload = json.dumps(original.to_dict(), ensure_ascii=False)
    restored = NormalizedSource.from_dict(json.loads(payload))
    assert restored == original


def test_content_unit_round_trips_with_optional_fields_none():
    unit = ContentUnit(unit_id="x", unit_type=UnitType.CODE, text="code")
    restored = ContentUnit.from_dict(unit.to_dict())
    assert restored == unit
    assert restored.hierarchy is None
    assert restored.locator is None
    assert restored.markdown is None


def test_unit_type_serializes_to_plain_string_not_enum():
    payload = _sample_source().to_dict()
    assert payload["units"][0]["unit_type"] == "heading"
    assert payload["units"][0]["unit_type"] == UnitType.HEADING.value


def test_locator_and_hierarchy_round_trip():
    locator = SourceLocator(
        page_start=1,
        page_end=2,
        bbox=[1.0, 2.0],
        file_path="f",
        line_start=5,
        line_end=9,
        symbol_name="foo",
        symbol_type="function",
        block_index=7,
    )
    hierarchy = Hierarchy(heading_path=["a", "b"], parent_unit_id="p", depth=2)
    assert SourceLocator.from_dict(locator.to_dict()) == locator
    assert Hierarchy.from_dict(hierarchy.to_dict()) == hierarchy


def test_empty_normalized_source_returns_empty_units_list():
    source = NormalizedSource(source_id="s")
    restored = NormalizedSource.from_dict(source.to_dict())
    assert restored == source
    assert restored.units == []
