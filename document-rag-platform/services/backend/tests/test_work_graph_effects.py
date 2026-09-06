from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.application.work_graph import (
    EffectCapability,
    EffectRequest,
    ScopeViolation,
    ScopedEffectContext,
    ScopedMutationDispatcher,
)


def _context(root: Path) -> ScopedEffectContext:
    return ScopedEffectContext(
        root=root,
        allowed_paths=("evidence/marker.json",),
        capabilities=(EffectCapability.WRITE_RECEIPT_MARKER,),
    )


def test_scoped_dispatcher_creates_and_rolls_back_exact_marker(tmp_path: Path) -> None:
    (tmp_path / "evidence").mkdir()
    dispatcher = ScopedMutationDispatcher(tmp_path)
    request = EffectRequest(
        capability=EffectCapability.WRITE_RECEIPT_MARKER,
        relative_path="evidence/marker.json",
        content=b'{"status":"verified"}\n',
    )
    result = dispatcher.dispatch(_context(tmp_path), request)
    target = tmp_path / request.relative_path
    assert target.read_bytes() == request.content
    assert result.before_artifact_hash == dispatcher.ABSENT_HASH
    assert result.output_artifact_hash != result.before_artifact_hash

    rolled_back = dispatcher.rollback(
        _context(tmp_path),
        request,
        expected_after_hash=result.output_artifact_hash,
    )
    assert not target.exists()
    assert rolled_back.output_artifact_hash == dispatcher.ABSENT_HASH
    assert rolled_back.rollback_result == {"status": "completed", "action": "unlink"}


@pytest.mark.parametrize("relative_path", ["../escape", "/tmp/escape", "evidence/../x"])
def test_scoped_dispatcher_rejects_path_escape(
    tmp_path: Path, relative_path: str
) -> None:
    (tmp_path / "evidence").mkdir()
    dispatcher = ScopedMutationDispatcher(tmp_path)
    with pytest.raises(ScopeViolation):
        dispatcher.dispatch(
            _context(tmp_path),
            EffectRequest(
                capability=EffectCapability.WRITE_RECEIPT_MARKER,
                relative_path=relative_path,
                content=b"blocked",
            ),
        )


def test_scoped_dispatcher_rejects_symlink_parent(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"outside-{os.getpid()}"
    outside.mkdir(exist_ok=False)
    try:
        os.symlink(outside, tmp_path / "evidence")
        dispatcher = ScopedMutationDispatcher(tmp_path)
        with pytest.raises(ScopeViolation, match="symlink"):
            dispatcher.dispatch(
                _context(tmp_path),
                EffectRequest(
                    capability=EffectCapability.WRITE_RECEIPT_MARKER,
                    relative_path="evidence/marker.json",
                    content=b"blocked",
                ),
            )
        assert list(outside.iterdir()) == []
    finally:
        outside.rmdir()
