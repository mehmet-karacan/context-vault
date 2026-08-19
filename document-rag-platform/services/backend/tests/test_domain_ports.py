"""Aşama 1: verifies the 10 domain ports in ``src.domain.ports`` import
cleanly and are genuinely ``typing.Protocol`` definitions (structural
interfaces), not e.g. plain classes or ABCs with a different contract.
"""

from src.domain import ports

PORT_NAMES = [
    "DocumentParser",
    "OcrProvider",
    "Chunker",
    "TokenCounter",
    "EmbeddingProvider",
    "VectorRetriever",
    "LexicalRetriever",
    "Reranker",
    "ObjectStorage",
    "SourceScanner",
]


def test_all_ten_ports_are_importable():
    for name in PORT_NAMES:
        assert hasattr(ports, name), f"missing port: {name}"
    assert len(PORT_NAMES) == 10


def test_all_ports_are_runtime_checkable_protocols():
    for name in PORT_NAMES:
        port = getattr(ports, name)
        assert getattr(port, "_is_protocol", False) is True, f"{name} is not a Protocol"
        assert getattr(port, "_is_runtime_protocol", False) is True, (
            f"{name} is not @runtime_checkable"
        )


def test_embedding_provider_accepts_a_structurally_conforming_object():
    class FakeEmbedder:
        def embed(self, texts):
            return [[0.0, 0.0] for _ in texts]

        def embed_one(self, text):
            return [0.0, 0.0]

    assert isinstance(FakeEmbedder(), ports.EmbeddingProvider)


def test_embedding_provider_rejects_a_non_conforming_object():
    class NotAnEmbedder:
        pass

    assert not isinstance(NotAnEmbedder(), ports.EmbeddingProvider)


def test_object_storage_rejects_partial_implementation():
    # Missing `delete` / `exists` — must not satisfy the ObjectStorage port.
    class PartialStorage:
        def put(self, key, data, content_type=None):
            return key

        def get(self, key):
            return b""

    assert not isinstance(PartialStorage(), ports.ObjectStorage)
