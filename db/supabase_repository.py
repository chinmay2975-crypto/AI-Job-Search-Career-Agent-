from typing import Any

from db.repository import Repository


class SupabaseRepository(Repository):
    """Postgres/Supabase implementation of Repository. Not yet implemented.

    Swap in via db/__init__.py once DATABASE_URL and Supabase credentials are configured;
    method signatures match SQLiteRepository so no other code needs to change.
    """

    def __init__(self, database_url: str):
        raise NotImplementedError("SupabaseRepository is a placeholder; implement once Supabase project exists")
