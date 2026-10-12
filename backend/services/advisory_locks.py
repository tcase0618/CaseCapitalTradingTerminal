"""Owner-scoped PostgreSQL session locks without using the application pool.

One dedicated connection owns each lock. It is held for the entire operation,
not returned to a pool (session locks are reentrant on the same connection).
Ending that exact session releases its lock without a TTL takeover race.
"""
import hashlib

from . import postgres_store


class SessionLock:
    def __init__(self, connection):
        self._connection = connection

    async def healthy(self) -> bool:
        connection = self._connection
        if connection is None or connection.is_closed():
            return False
        try:
            return await connection.fetchval("select 1", timeout=5) == 1
        except Exception:
            return False

    async def release(self) -> None:
        connection, self._connection = self._connection, None
        if connection is None:
            return
        try:
            await connection.close(timeout=5)
        except BaseException:
            # Termination also ends the owning session if graceful cleanup
            # failed or was cancelled. Never unlock via a different session.
            connection.terminate()
            raise


async def try_acquire(namespace: str) -> SessionLock | None:
    if not postgres_store.enabled() or not postgres_store.dsn() or postgres_store.asyncpg is None:
        raise RuntimeError("PostgreSQL session lock unavailable")
    key = int.from_bytes(hashlib.sha256(namespace.encode("utf-8")).digest()[:8], "big", signed=True)
    connection = await postgres_store.asyncpg.connect(postgres_store.dsn(), timeout=5, command_timeout=5)
    lock = SessionLock(connection)
    try:
        if await connection.fetchval("select pg_try_advisory_lock($1::bigint)", key):
            return lock
        await lock.release()
        return None
    except BaseException:
        await lock.release()
        raise
