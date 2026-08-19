"""Tests for the canonical-path security guard (Aşama 7.2)."""

import os

import pytest

from src.infrastructure.repositories.path_security import (
    canonical,
    canonical_under_root,
    is_allowed_scan_path,
)


def test_canonical_under_root_accepts_root_and_children(tmp_path):
    root = str(tmp_path)
    child = os.path.join(root, "sub", "file.py")
    os.makedirs(os.path.dirname(child))

    assert canonical_under_root(child, root) is True
    assert canonical_under_root(root, root) is True
    assert canonical_under_root(os.path.join(root, "sub"), root) is True


def test_canonical_under_root_rejects_outside(tmp_path):
    root = str(tmp_path / "root")
    outside = str(tmp_path / "outside")
    os.makedirs(root)
    os.makedirs(outside)
    os.makedirs(os.path.join(root, "sub"))

    assert canonical_under_root(outside, root) is False
    evil = os.path.join(root, "sub", "..", "..", "outside")
    assert canonical_under_root(evil, root) is False


def test_is_allowed_scan_path_accepts_under_root(tmp_path):
    root = str(tmp_path / "workspace")
    target = os.path.join(root, "project-a", "src")
    os.makedirs(target)

    assert is_allowed_scan_path(target, [root]) is True


def test_is_allowed_scan_path_rejects_outside_and_relative(tmp_path):
    root = str(tmp_path / "workspace")
    outside = str(tmp_path / "elsewhere")
    os.makedirs(root)
    os.makedirs(outside)

    assert is_allowed_scan_path(outside, [root]) is False
    assert is_allowed_scan_path("relative/path", [root]) is False
    assert is_allowed_scan_path("", [root]) is False
    assert is_allowed_scan_path(outside, []) is False


def test_is_allowed_scan_path_accepts_comma_string(tmp_path):
    root = str(tmp_path / "workspace")
    os.makedirs(root)
    assert is_allowed_scan_path(root, "{0},/imports".format(root)) is True


def test_traversal_under_root_rejected(tmp_path):
    root = str(tmp_path / "workspace")
    os.makedirs(root)
    escaping = os.path.join(root, "..", "workspace", "..", str(tmp_path.name), "..")
    assert is_allowed_scan_path(escaping, [root]) is False


def test_symlink_escape_rejected(tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("private")

    link = root / "leak"
    try:
        os.symlink(str(outside), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted in this environment")

    through_link = os.path.join(str(link), "secret.txt")
    assert os.path.isabs(through_link)
    # realpath resolves the symlink to a path above the allowed root.
    assert canonical(through_link) == os.path.realpath(str(secret))
    assert canonical_under_root(through_link, str(root)) is False
    assert is_allowed_scan_path(through_link, [str(root)]) is False
