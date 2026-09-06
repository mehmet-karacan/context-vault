from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from .config import settings
from .infrastructure.observability import install_sqlalchemy_metrics
from .migration_settings import EXPECTED_ALEMBIC_HEAD

DATABASE_URL = settings.DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
install_sqlalchemy_metrics(engine)


@event.listens_for(engine, "connect")
def _register_vector_type(dbapi_connection, connection_record):
    try:
        register_vector(dbapi_connection)
    except Exception:
        # A database that has not reached the migration head may not expose
        # the vector type yet. Roll back the failed type lookup; the startup
        # revision admission check below will then fail closed.
        dbapi_connection.rollback()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def database_identity_is_admitted(conn, configuration=settings) -> bool:
    expected_role = configuration.EXPECTED_DATABASE_ROLE
    if not expected_role:
        return not configuration.is_production
    row = conn.execute(
        text(
            """
            SELECT
              current_user,
              rolsuper,
              rolcreaterole,
              rolcreatedb,
              rolinherit,
              rolreplication,
              rolbypassrls,
              EXISTS (
                SELECT 1
                  FROM pg_auth_members
                 WHERE member = (SELECT oid FROM pg_roles WHERE rolname = current_user)
              ) AS has_membership
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
    ).one()
    return row == (
        expected_role,
        False,
        False,
        False,
        False,
        False,
        False,
        False,
    )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Verify schema admission without creating or repairing database objects."""
    with engine.connect() as conn:
        try:
            if not database_identity_is_admitted(conn):
                raise RuntimeError(
                    "Database runtime identity does not match the expected "
                    "least-privilege role"
                )
            current = conn.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        except Exception as exc:
            raise RuntimeError(
                "Database schema is unavailable or not managed by Alembic; "
                "run the documented migration command before application startup"
            ) from exc

    if current != EXPECTED_ALEMBIC_HEAD:
        raise RuntimeError(
            "Database migration revision mismatch: "
            f"expected {EXPECTED_ALEMBIC_HEAD}, observed {current}"
        )
