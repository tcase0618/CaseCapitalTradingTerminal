from pathlib import Path

from services import postgres_migrations


def test_migration_manifest_is_ordered_and_concurrent_index_is_standalone():
    migrations = postgres_migrations.available_migrations()

    assert [migration.version for migration in migrations] == sorted(migration.version for migration in migrations)
    assert migrations
    assert migrations[0].path == Path(postgres_migrations.MIGRATIONS_DIR / "001_snapshot_payload_gin.sql")
    assert "CREATE INDEX CONCURRENTLY" in migrations[0].sql.upper()
    assert migrations[0].sql.rstrip().endswith(";")
