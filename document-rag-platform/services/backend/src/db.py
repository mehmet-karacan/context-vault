from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from .config import settings
from .migration_settings import EXPECTED_ALEMBIC_HEAD

DATABASE_URL = settings.DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


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
