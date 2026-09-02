"""Startup schema admission is read-only and fail-closed."""

from contextlib import contextmanager

import pytest

from src import db as db_module


class _Result:
    def __init__(self, revision):
        self.revision = revision

    def scalar_one(self):
        return self.revision


class _Connection:
    def __init__(self, revision=None, error=None):
        self.revision = revision
        self.error = error
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.error:
            raise self.error
        return _Result(self.revision)


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection


def test_init_db_accepts_exact_revision_without_ddl(monkeypatch):
    connection = _Connection(db_module.EXPECTED_ALEMBIC_HEAD)
    monkeypatch.setattr(db_module, "engine", _Engine(connection))

    db_module.init_db()

    assert connection.statements == ["SELECT version_num FROM alembic_version"]


def test_init_db_rejects_revision_drift(monkeypatch):
    connection = _Connection("unexpected")
    monkeypatch.setattr(db_module, "engine", _Engine(connection))

    with pytest.raises(RuntimeError, match="revision mismatch"):
        db_module.init_db()


def test_init_db_rejects_unmanaged_database(monkeypatch):
    connection = _Connection(error=RuntimeError("missing table"))
    monkeypatch.setattr(db_module, "engine", _Engine(connection))

    with pytest.raises(RuntimeError, match="not managed by Alembic"):
        db_module.init_db()
