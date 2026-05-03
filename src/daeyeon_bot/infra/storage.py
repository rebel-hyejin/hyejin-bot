"""SQLite storage adapter (aiosqlite + WAL pragma).

Phase 0: stub. The connection factory and migration runner land in Phase 1.
"""

from __future__ import annotations


async def open_db() -> None:
    raise NotImplementedError("Phase 1: aiosqlite connection factory + WAL pragma")


async def apply_migrations() -> None:
    raise NotImplementedError(
        "Phase 1: read infra/db/migrations/*.sql, advance meta.schema_version"
    )
