import os

from db.repository import Repository
from db.sqlite_repository import SQLiteRepository


def get_repository() -> Repository:
    if os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_KEY"):
        from db.supabase_repository import SupabaseRepository

        return SupabaseRepository()
    return SQLiteRepository()
