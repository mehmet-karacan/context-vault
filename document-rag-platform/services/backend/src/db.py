from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .config import settings

DATABASE_URL = settings.DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


@event.listens_for(engine, "connect")
def _register_vector_type(dbapi_connection, connection_record):
    try:
        register_vector(dbapi_connection)
    except Exception:
        # First-ever connection may run before `CREATE EXTENSION vector`
        # (see init_db below); later connections register fine. Roll back
        # so the failed lookup doesn't leave the transaction aborted.
        dbapi_connection.rollback()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

    from . import models  # noqa: F401  (kept so Base.metadata is fully populated
    # for anything that still inspects it, e.g. tests)

    # Schema creation is Alembic's job now (see alembic/versions/ and
    # MIGRATION_RUNBOOK.md), not app startup's. This function used to call
    # Base.metadata.create_all(bind=engine) here unconditionally, which
    # silently created tables Alembic didn't know about and collided with
    # `alembic upgrade` ("relation ... already exists"). Run
    # `alembic upgrade head` before starting the app against a fresh
    # database; startup no longer creates or alters tables.

    with engine.connect() as conn:
        try:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS chunks_embedding_idx "
                    "ON chunks USING hnsw (embedding vector_cosine_ops)"
                )
            )
            conn.commit()
        except Exception:
            # HNSW requires pgvector >= 0.5.0; harmless to skip on older images,
            # search still works without the index at this data volume.
            conn.rollback()
