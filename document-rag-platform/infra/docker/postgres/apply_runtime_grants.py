#!/usr/bin/env python3
"""Apply least-privilege grants after Alembic without exposing credentials."""

from __future__ import annotations

import os
import re
import sys

import psycopg2
from psycopg2 import sql


SAFE_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class GrantError(RuntimeError):
    pass


def required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise GrantError(f"missing required deployment setting: {name}")
    return value


def apply_runtime_grants() -> None:
    database_url = required_environment("DATABASE_URL")
    runtime_role = required_environment("POSTGRES_RUNTIME_USER")
    schema = required_environment("DATABASE_SCHEMA")
    if not SAFE_IDENTIFIER.fullmatch(runtime_role):
        raise GrantError("runtime role identifier is invalid")
    if not SAFE_IDENTIFIER.fullmatch(schema):
        raise GrantError("database schema identifier is invalid")

    try:
        connection = psycopg2.connect(
            database_url,
            connect_timeout=5,
            application_name="context-vault-runtime-grants",
        )
        connection.autocommit = False
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_user, current_database()")
            migration_role, database = cursor.fetchone()
            if migration_role == runtime_role:
                raise GrantError("migration and runtime database roles must differ")

            cursor.execute(
                """
                SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolinherit,
                       rolreplication, rolbypassrls
                  FROM pg_roles
                 WHERE rolname = %s
                """,
                (runtime_role,),
            )
            attributes = cursor.fetchone()
            if attributes is None:
                raise GrantError("runtime database role does not exist")
            if attributes != (True, False, False, False, False, False, False):
                raise GrantError("runtime database role has privileged attributes")

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM pg_auth_members
                     WHERE member = (SELECT oid FROM pg_roles WHERE rolname = %s)
                )
                """,
                (runtime_role,),
            )
            if cursor.fetchone()[0]:
                raise GrantError("runtime database role has role memberships")

            cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,))
            if cursor.fetchone() is None:
                raise GrantError("application database schema does not exist")

            # Role ownership is recorded centrally in pg_shdepend for every
            # object class (relations, functions, types, collations, operators,
            # and future object kinds). Reject any current-database ownership
            # rather than trying to maintain an incomplete catalog allow-list.
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                      FROM pg_shdepend d
                     WHERE d.refclassid = 'pg_authid'::regclass
                       AND d.refobjid = (
                           SELECT oid FROM pg_roles WHERE rolname = %s
                       )
                       AND d.deptype = 'o'
                       AND d.dbid = (
                           SELECT oid FROM pg_database
                            WHERE datname = current_database()
                       )
                )
                """,
                (runtime_role,),
            )
            if cursor.fetchone()[0]:
                raise GrantError("runtime database role owns database objects")

            cursor.execute(
                """
                SELECT
                  EXISTS (
                    SELECT 1
                      FROM pg_class c
                      JOIN pg_namespace n ON n.oid = c.relnamespace
                     WHERE n.nspname = %s
                       AND c.relowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
                  ),
                  EXISTS (
                    SELECT 1
                      FROM pg_proc p
                      JOIN pg_namespace n ON n.oid = p.pronamespace
                     WHERE n.nspname = %s
                       AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
                  ),
                  EXISTS (
                    SELECT 1
                      FROM pg_type t
                      JOIN pg_namespace n ON n.oid = t.typnamespace
                     WHERE n.nspname = %s
                       AND t.typrelid = 0
                       AND t.typowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
                  )
                """,
                (
                    schema,
                    runtime_role,
                    schema,
                    runtime_role,
                    schema,
                    runtime_role,
                ),
            )
            owns_relation, owns_function, owns_type = cursor.fetchone()
            if owns_relation or owns_function or owns_type:
                raise GrantError("runtime database role owns application objects")

            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(
                    sql.Identifier(schema)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                    sql.Identifier(schema), sql.Identifier(runtime_role)
                )
            )
            cursor.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(database), sql.Identifier(runtime_role)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(database)
                )
            )
            cursor.execute(
                sql.SQL("REVOKE TEMPORARY ON DATABASE {} FROM {}").format(
                    sql.Identifier(database), sql.Identifier(runtime_role)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(runtime_role)
                )
            )
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES "
                    "IN SCHEMA {} TO {}"
                ).format(sql.Identifier(schema), sql.Identifier(runtime_role))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}"
                ).format(sql.Identifier(schema), sql.Identifier(runtime_role))
            )
            cursor.execute(
                sql.SQL(
                    "REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA {} FROM PUBLIC"
                ).format(sql.Identifier(schema))
            )
            cursor.execute(
                sql.SQL("GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(runtime_role)
                )
            )

            for statement in (
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}",
                "GRANT USAGE, SELECT ON SEQUENCES TO {}",
                "REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC",
                "GRANT EXECUTE ON FUNCTIONS TO {}",
            ):
                query = sql.SQL(
                    "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} " + statement
                ).format(
                    sql.Identifier(migration_role),
                    sql.Identifier(schema),
                    *((sql.Identifier(runtime_role),) if "{}" in statement else ()),
                )
                cursor.execute(query)

            cursor.execute(
                """
                SELECT
                  has_database_privilege(%s, current_database(), 'CONNECT'),
                  has_database_privilege(%s, current_database(), 'TEMPORARY'),
                  has_schema_privilege(%s, %s, 'USAGE'),
                  has_schema_privilege(%s, %s, 'CREATE'),
                  EXISTS (
                    SELECT 1 FROM pg_database
                     WHERE datname = current_database()
                       AND datdba = (SELECT oid FROM pg_roles WHERE rolname = %s)
                  ),
                  EXISTS (
                    SELECT 1 FROM pg_namespace
                     WHERE nspname = %s
                       AND nspowner = (SELECT oid FROM pg_roles WHERE rolname = %s)
                  )
                """,
                (
                    runtime_role,
                    runtime_role,
                    runtime_role,
                    schema,
                    runtime_role,
                    schema,
                    runtime_role,
                    schema,
                    runtime_role,
                ),
            )
            connect_ok, temp_ok, usage_ok, create_ok, owns_database, owns_schema = (
                cursor.fetchone()
            )
            if (
                not connect_ok
                or temp_ok
                or not usage_ok
                or create_ok
                or owns_database
                or owns_schema
            ):
                raise GrantError("runtime database role verification failed")
    except GrantError:
        raise
    except Exception as error:
        raise GrantError("database grant operation failed") from error
    finally:
        if "connection" in locals():
            connection.close()


def main() -> int:
    try:
        apply_runtime_grants()
    except GrantError as error:
        print(f"runtime database grants: FAIL ({error})", file=sys.stderr)
        return 1
    print("runtime database grants: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
