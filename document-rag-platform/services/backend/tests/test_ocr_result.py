"""OcrResult / OcrBlock dataclass tests (Aşama 8.1).

Verifies the contract shape per AKTIF_GOREV.md 8.1 (all fields, both on the
result and on each block) and lossless ``to_dict``/``from_dict`` serialization
so an OCR result can be stored as a ``cr_json`` artifact.
"""

from src.infrastructure.ocr import (
    CONFIDENCE,
    NEEDS_REVIEW,
    OcrBlock,
    OcrResult,
)


def test_ocr_result_has_all_contract_fields():
    result = OcrResult(
        full_text="hello world",
        blocks=[
            OcrBlock(text="hello", bbox=[0, 0, 10, 10], confidence=0.9),
            OcrBlock(text="world", bbox=[12, 0, 12, 10], confidence=0.8),
        ],
        confidence=0.85,
        language="tur+eng",
        orientation=0,
        preprocessing_steps=["denoise"],
        engine="tesseract",
        engine_version="tesseract 5.3.0",
    )
    for attr in (
        "full_text",
        "blocks",
        "confidence",
        "language",
        "orientation",
        "preprocessing_steps",
        "engine",
        "engine_version",
    ):
        assert hasattr(result, attr)


def test_ocr_block_has_all_contract_fields():
    block = OcrBlock(
        text="x",
        bbox=[1, 2, 3, 4],
        confidence=0.99,
        page_number=0,
        reading_order=5,
    )
    for attr in ("text", "bbox", "confidence", "page_number", "reading_order"):
        assert hasattr(block, attr)


def test_defaults_are_sane():
    result = OcrResult()
    assert result.full_text == ""
    assert result.blocks == []
    assert result.confidence is None
    assert result.language is None
    assert result.orientation == 0
    assert result.preprocessing_steps == []
    assert result.engine is None
    assert result.engine_version is None

    block = OcrBlock(text="t")
    assert block.bbox is None
    assert block.confidence is None
    assert block.page_number == 0
    assert block.reading_order == 0


def test_to_dict_roundtrip():
    result = OcrResult(
        full_text="a b",
        blocks=[OcrBlock(text="a", bbox=[0, 0, 4, 4], confidence=0.5)],
        confidence=0.5,
        language="eng",
        orientation=0,
        preprocessing_steps=["contrast"],
        engine="tesseract",
        engine_version="tesseract 1.0",
    )
    data = result.to_dict()
    # JSON-safe basics for cr_json artifact storage.
    assert isinstance(data["blocks"], list)
    assert data["blocks"][0]["text"] == "a"
    assert data["blocks"][0]["bbox"] == [0, 0, 4, 4]

    restored = OcrResult.from_dict(data)
    assert restored == result
    assert restored.to_dict() == data


def test_needs_review_high_confidence_is_false():
    result = OcrResult(
        blocks=[OcrBlock(text="ok", confidence=0.9)],
        confidence=0.9,
    )
    assert result.needs_review(0.60) is False
    assert result.metadata(0.60)[NEEDS_REVIEW] is False
    assert result.metadata(0.60)[CONFIDENCE] == 0.9


def test_needs_review_low_confidence_is_true():
    result = OcrResult(
        blocks=[OcrBlock(text="blurry", confidence=0.3)],
        confidence=0.3,
    )
    assert result.needs_review(0.60) is True
    assert result.metadata(0.60)[NEEDS_REVIEW] is True


def test_aggregate_confidence_is_length_weighted():
    # Two blocks, same confidence -> average; heavier text weighs more.
    result = OcrResult(
        blocks=[
            OcrBlock(text="a", confidence=0.5),
            OcrBlock(text="aaaaaaaaaa", confidence=1.0),
        ]
    )
    # (0.5*1 + 1.0*10)/11 = 10.5/11 ~= 0.9545
    assert result.aggregate_confidence == 0.9545
