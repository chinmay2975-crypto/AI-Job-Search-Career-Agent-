from db.repository import Repository
from db.sqlite_repository import SQLiteRepository


def get_repository() -> Repository:
    """Local SQLite database (data/career_agent.db, or SQLITE_DB_PATH)."""
    return SQLiteRepository()
