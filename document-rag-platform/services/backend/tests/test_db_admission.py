"""Startup schema admission is read-only and fail-closed."""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src import db as db_module


class _Result:
    def __init__(self, revision=None, row=None):
        self.revision = revision
        self.row = row

    def scalar_one(self):
        return self.revision

    def one(self):
        return self.row


class _Connection:
    def __init__(self, revision=None, error=None, identity=None):
        self.revision = revision
        self.error = error
        self.identity = identity
        self.statements = []

    def execute(self, statement):
        self.statements.append(str(statement))
        if self.error:
            raise self.error
        if "current_user" in str(statement):
            return _Result(row=self.identity)
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


def test_database_identity_requires_expected_unprivileged_role():
    admitted = (
        "context_vault_app",
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    )
    configuration = SimpleNamespace(
        EXPECTED_DATABASE_ROLE="context_vault_app", is_production=True
    )
    connection = _Connection(identity=admitted)

    assert db_module.database_identity_is_admitted(connection, configuration) is True

    wrong_role = _Connection(identity=("context_vault_owner", *admitted[1:]))
    assert db_module.database_identity_is_admitted(wrong_role, configuration) is False

    privileged = _Connection(
        identity=("context_vault_app", True, False, False, False, False, False, False)
    )
    assert db_module.database_identity_is_admitted(privileged, configuration) is False

    member = _Connection(identity=(*admitted[:-1], True))
    assert db_module.database_identity_is_admitted(member, configuration) is False


def test_database_identity_is_required_in_production():
    production = SimpleNamespace(EXPECTED_DATABASE_ROLE=None, is_production=True)
    local = SimpleNamespace(EXPECTED_DATABASE_ROLE=None, is_production=False)

    assert db_module.database_identity_is_admitted(_Connection(), production) is False
    assert db_module.database_identity_is_admitted(_Connection(), local) is True
