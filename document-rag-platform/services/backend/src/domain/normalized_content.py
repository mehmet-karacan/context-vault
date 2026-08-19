"""Ortak Normalize Edilmiş İçerik Modeli (Aşama 3).

DOCX, PDF, görsel, OCR ve kaynak kodu aynı ingestion altyapısına bağlamak
için ortak bir ara model tanımlar (bkz. AKTIF_GOREV.md Bölüm 6).

Parser'lar doğrudan chunk üretmez; önce bu normalize içerik modelini üretir.
Normalize model kayıpsız veya yeniden üretilebilir JSON olarak saklanır
(Bölüm 6 kuralı) — bu yüzden her sınıf ``to_dict()`` / ``from_dict()``
yardımcıları sunar; ``unit_type`` gibi Enum değerleri JSON için temel tipe
indirgenir.

Tüm alanlar datum değildir; varsayılanların çoğu ``None`` ya da boş kaptır,
böylece her parser yalnız elinde olan bilgiyi doldurur ve gerisini JSON'da
``null`` / ``[]`` olarak korur (kayıpsız serileştirme).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class UnitType(str, Enum):
    """Zorunlu `unit_type` değerleri (AKTIF_GOREV.md Bölüm 6)."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CODE = "code"
    FORMULA = "formula"
    IMAGE = "image"
    IMAGE_CAPTION = "image_caption"
    OCR_TEXT = "ocr_text"
    PAGE_BREAK = "page_break"
    FILE_HEADER = "file_header"
    SYMBOL = "symbol"
    CONFIGURATION = "configuration"


# Kapsamlı unit_type setinin soyut bir sabiti (belgeleme / dış kullanım için).
UNIT_TYPES: Tuple[str, ...] = tuple(member.value for member in UnitType)


@dataclass
class SourceLocator:
    """Bir içerik biriminin kaynak konumu (Bölüm 6).

    DOCX için gerçek sayfa numarası garanti edilmez; orada başlık yolu ve
    blok sırası temel citation olur. PDF/görselde sayfa ve bounding-box,
    kodda dosya yolu + satır aralığı + sembol bilgisi korunur.
    """

    page_start: Optional[int] = None
    page_end: Optional[int] = None
    bbox: Optional[List[float]] = None
    file_path: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    symbol_name: Optional[str] = None
    symbol_type: Optional[str] = None
    block_index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page_start": self.page_start,
            "page_end": self.page_end,
            "bbox": self.bbox,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "block_index": self.block_index,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SourceLocator":
        return cls(
            page_start=data.get("page_start"),
            page_end=data.get("page_end"),
            bbox=data.get("bbox"),
            file_path=data.get("file_path"),
            line_start=data.get("line_start"),
            line_end=data.get("line_end"),
            symbol_name=data.get("symbol_name"),
            symbol_type=data.get("symbol_type"),
            block_index=data.get("block_index"),
        )


@dataclass
class Hierarchy:
    """Bir içerik biriminin başlık hiyerarşisindeki yeri.

    ``parent_unit_id``, aynı NormalizedSource içindeki bir ContentUnit'e
    işaret eder (retrieval'da komşu/parent genişletme için).
    """

    heading_path: List[str] = field(default_factory=list)
    parent_unit_id: Optional[str] = None
    depth: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "heading_path": list(self.heading_path),
            "parent_unit_id": self.parent_unit_id,
            "depth": self.depth,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Hierarchy":
        return cls(
            heading_path=list(data.get("heading_path") or []),
            parent_unit_id=data.get("parent_unit_id"),
            depth=data.get("depth", 0),
        )


@dataclass
class ContentUnit:
    """Normalize içeriğin tek bir atomik birimi (Bölüm 6)."""

    unit_id: str
    unit_type: UnitType
    text: str = ""
    markdown: Optional[str] = None
    order: int = 0
    hierarchy: Optional[Hierarchy] = None
    locator: Optional[SourceLocator] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "unit_type": self.unit_type.value,
            "text": self.text,
            "markdown": self.markdown,
            "order": self.order,
            "hierarchy": self.hierarchy.to_dict() if self.hierarchy else None,
            "locator": self.locator.to_dict() if self.locator else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentUnit":
        hierarchy = data.get("hierarchy")
        locator = data.get("locator")
        return cls(
            unit_id=data["unit_id"],
            unit_type=UnitType(data["unit_type"]),
            text=data.get("text", ""),
            markdown=data.get("markdown"),
            order=data.get("order", 0),
            hierarchy=Hierarchy.from_dict(hierarchy) if hierarchy else None,
            locator=SourceLocator.from_dict(locator) if locator else None,
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class NormalizedSource:
    """Bir kaynak dosyanın tam normalize edilmiş içeriği (Bölüm 6).

    ``source_id`` her parse çağrısı için benzersizdir (ör. ``uuid4``);
    ``version_id`` varsa dışarıdan (ingestion/version) atanır.
    """

    source_id: str
    version_id: Optional[str] = None
    source_type: str = ""
    title: Optional[str] = None
    language: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    units: List[ContentUnit] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "version_id": self.version_id,
            "source_type": self.source_type,
            "title": self.title,
            "language": self.language,
            "metadata": dict(self.metadata),
            "units": [unit.to_dict() for unit in self.units],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NormalizedSource":
        return cls(
            source_id=data["source_id"],
            version_id=data.get("version_id"),
            source_type=data.get("source_type", ""),
            title=data.get("title"),
            language=data.get("language"),
            metadata=dict(data.get("metadata") or {}),
            units=[ContentUnit.from_dict(u) for u in data.get("units") or []],
        )
