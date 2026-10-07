"""Small, explicit PostgreSQL migration runner for Case Capital.

Migrations are deliberately separate from application startup.  Some schema
changes, including ``CREATE INDEX CONCURRENTLY``, cannot run inside a
transaction and should not make a trading service start unexpectedly slow.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
LOCK_KEY = 5_284_202_610


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    sql: str


def available_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
        sql = path.read_text(encoding="utf-8").strip()
        if sql:
            migrations.append(Migration(version=path.stem.split("_", 1)[0], path=path, sql=sql))
    return migrations


async def apply_pending_migrations(pool: Any) -> dict[str, Any]:
    """Apply each version once and return an auditable result.

    The advisory lock serializes deploys.  SQL runs outside an explicit
    transaction so a migration may safely contain concurrent index creation.
    Every migration file must therefore be independently idempotent.
    """
    applied: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cc_schema_migrations (
              version text PRIMARY KEY,
              filename text NOT NULL,
              applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            completed = {
                row["version"]
                for row in await conn.fetch("SELECT version FROM cc_schema_migrations")
            }
            for migration in available_migrations():
                if migration.version in completed:
                    continue
                await conn.execute(migration.sql)
                await conn.execute(
                    "INSERT INTO cc_schema_migrations(version, filename) VALUES($1, $2)",
                    migration.version,
                    migration.path.name,
                )
                applied.append(migration.path.name)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    return {"ok": True, "applied": applied, "available": [m.path.name for m in available_migrations()]}


async def migration_status(pool: Any) -> dict[str, Any]:
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.cc_schema_migrations')")
        completed = []
        if exists:
            completed = [row["version"] for row in await conn.fetch("SELECT version FROM cc_schema_migrations ORDER BY version")]
    available = [m.version for m in available_migrations()]
    return {
        "ok": True,
        "applied": completed,
        "pending": [version for version in available if version not in set(completed)],
    }
