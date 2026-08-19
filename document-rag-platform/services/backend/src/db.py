import os

from pgvector.psycopg2 import register_vector
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://user:password@localhost:5432/rag_platform"
)

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

    from . import models  # noqa: F401  (register models on Base before create_all)

    Base.metadata.create_all(bind=engine)

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
