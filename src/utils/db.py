"""
Database connection management.

SQLAlchemy is the standard Python library for working with relational databases.
It provides two things:
1. An engine — manages the connection pool to PostgreSQL
2. A session — a unit of work; all your queries within a session are atomic

We never create connections directly. We use the engine and session factory
defined here, imported wherever needed.
"""
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from src.utils.config import settings


# The engine manages a pool of database connections
# pool_pre_ping=True means SQLAlchemy will test the connection
# before using it — prevents errors from stale connections
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    echo=False,  # Set to True to print all SQL queries (useful for debugging)
)

# SessionLocal is a factory for creating database sessions
SessionLocal = sessionmaker(
    autocommit=False,   # We control when transactions are committed
    autoflush=False,    # We control when changes are flushed to the DB
    bind=engine,
)


@contextmanager
def get_db_session() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.
    
    Usage:
        with get_db_session() as session:
            result = session.execute(text("SELECT 1"))
    
    The 'with' block guarantees:
    - The session is created before the block
    - The session is committed if the block succeeds
    - The session is rolled back if any exception occurs
    - The session is always closed, even if an error occurs
    
    This prevents database connection leaks.
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def verify_connection() -> bool:
    """
    Test that the database is reachable.
    Call this at application startup to fail fast if the DB is unreachable.
    """
    try:
        with get_db_session() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"Database connection failed: {e}")
        return False