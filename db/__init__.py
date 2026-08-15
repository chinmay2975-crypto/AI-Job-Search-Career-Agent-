import os

from db.repository import Repository
from db.sqlite_repository import SQLiteRepository


def get_repository() -> Repository:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        from db.supabase_repository import SupabaseRepository

        return SupabaseRepository(database_url)
    return SQLiteRepository()
