"""Structural guards for the canonical ingestion path."""

from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def test_production_has_one_ingestion_orchestrator() -> None:
    python_sources = list(SRC_ROOT.rglob("*.py"))
    orchestrator_definitions = [
        path
        for path in python_sources
        if "class IngestionOrchestrator" in path.read_text(encoding="utf-8")
    ]
    assert orchestrator_definitions == [
        SRC_ROOT / "application" / "ingestion_orchestrator.py"
    ]
    assert not (SRC_ROOT / "application" / "reindex_service.py").exists()


def test_api_does_not_define_or_import_legacy_parser_chunker() -> None:
    api_source = (SRC_ROOT / "api" / "v1" / "documents.py").read_text(encoding="utf-8")
    assert "def extract_text(" not in api_source
    assert "def chunk_text(" not in api_source
    assert "from ...workers.ingestion_tasks import extract_text" not in api_source
    assert "from ...workers.ingestion_tasks import chunk_text" not in api_source


def test_async_ingestion_has_no_dual_path_feature_flag() -> None:
    for path in SRC_ROOT.rglob("*.py"):
        assert "FEATURE_ASYNC_INGESTION" not in path.read_text(encoding="utf-8")
