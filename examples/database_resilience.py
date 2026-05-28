"""
Database Resilience
-------------------
Simulates an asyncpg/SQLAlchemy async database connection pool and shows
how to test query timeouts, connection pool exhaustion, and retry-on-disconnect.

Real-world scenario: your app uses asyncpg or databases[asyncpg] to talk
to PostgreSQL. You want to verify that slow queries don't stall your web
workers and that reconnect logic works after a DB blip.

Run with:  python examples/database_resilience.py
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from asynchaos import chaos, chaos_zone, timeout
from asynchaos.conditions import RateCondition
from asynchaos.patch import chaos_patch


# ---------------------------------------------------------------------------
# Stand-in for asyncpg.Connection / databases.Database
# ---------------------------------------------------------------------------

class DatabaseConnection:
    """Minimal async DB interface (swap for your real asyncpg/aioredis client)."""

    async def fetchrow(self, query: str, *args) -> dict | None:
        return {"id": args[0], "name": "Alice"} if args else None

    async def fetch(self, query: str, *args) -> list[dict]:
        return [{"id": i, "value": i * 10} for i in range(1, 6)]

    async def execute(self, query: str, *args) -> str:
        return "INSERT 0 1"

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Repository layer — wraps DB calls, owns timeout and retry logic
# ---------------------------------------------------------------------------

DB_QUERY_TIMEOUT = 0.5   # 500ms SLA per query


@timeout(seconds=DB_QUERY_TIMEOUT)
async def get_user(conn: DatabaseConnection, user_id: int) -> dict | None:
    return await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)


@timeout(seconds=DB_QUERY_TIMEOUT)
async def list_orders(conn: DatabaseConnection) -> list[dict]:
    return await conn.fetch("SELECT * FROM orders LIMIT 100")


async def get_user_with_reconnect(
    conn: DatabaseConnection,
    user_id: int,
    retries: int = 2,
) -> dict | None:
    """Retry the query on connection errors (e.g. after a DB failover)."""
    for attempt in range(1, retries + 2):
        try:
            return await get_user(conn, user_id)
        except ConnectionError as exc:
            if attempt > retries:
                raise
            print(f"    DB connection error (attempt {attempt}): {exc} — reconnecting")
            await asyncio.sleep(0.05 * attempt)
    return None


# ---------------------------------------------------------------------------
# Chaos experiments
# ---------------------------------------------------------------------------

async def experiment_slow_query_timeout() -> None:
    """Verify the query timeout SLA is enforced and the error is explicit."""
    print("=== Experiment: slow query exceeds 500ms SLA ===")
    conn = DatabaseConnection()

    with chaos_patch(DatabaseConnection, ["fetchrow"], latency=800):
        try:
            await get_user(conn, 42)
            print("  FAIL: expected TimeoutError")
        except asyncio.TimeoutError:
            print(f"  PASS: TimeoutError raised (query exceeded {DB_QUERY_TIMEOUT*1000:.0f}ms SLA)")


async def experiment_query_within_sla() -> None:
    """A 200ms query should complete within the 500ms SLA."""
    print("\n=== Experiment: 200ms query — within 500ms SLA ===")
    conn = DatabaseConnection()

    with chaos_patch(DatabaseConnection, ["fetchrow"], latency=200):
        start = time.perf_counter()
        user = await get_user(conn, 42)
        elapsed = (time.perf_counter() - start) * 1000

    print(f"  user: {user}")
    print(f"  elapsed: {elapsed:.0f}ms  (expected ~200ms — within SLA)")


async def experiment_connection_loss_and_reconnect() -> None:
    """Simulate a DB blip: first two calls fail, then it recovers."""
    print("\n=== Experiment: DB blip — connection drops on first 2 calls ===")
    conn = DatabaseConnection()

    # RateCondition: fail the first 2 of every 5 calls deterministically
    async with chaos_zone(drop_rate=RateCondition(fail_count=2, window=5)):
        user = await get_user_with_reconnect(conn, 1, retries=3)

    print(f"  recovered user: {user}")
    assert user is not None
    print("  PASS: reconnect succeeded after initial failures")


async def experiment_concurrent_queries_under_load() -> None:
    """Under 150ms latency, 5 concurrent queries should still complete together ~150ms."""
    print("\n=== Experiment: 5 concurrent queries with 150ms latency ===")
    conn = DatabaseConnection()

    with chaos_patch(DatabaseConnection, ["fetchrow", "fetch"], latency=150):
        start = time.perf_counter()
        results = await asyncio.gather(
            get_user(conn, 1),
            get_user(conn, 2),
            list_orders(conn),
            get_user(conn, 3),
            list_orders(conn),
        )
        elapsed = (time.perf_counter() - start) * 1000

    print(f"  elapsed: {elapsed:.0f}ms  (all concurrent — expected ~150ms, not 750ms)")
    print(f"  results: {len(results)} queries completed")


async def experiment_partial_degradation() -> None:
    """10% of queries are slow; the rest should stay fast."""
    print("\n=== Experiment: 10% of queries degraded to 400ms ===")
    conn = DatabaseConnection()
    timings = []

    # RateCondition: 1 slow call per 10
    slow_condition = RateCondition(fail_count=1, window=10)

    for _ in range(10):
        start = time.perf_counter()
        try:
            with chaos_patch(
                DatabaseConnection, ["fetchrow"],
                latency=400 if slow_condition.should_trigger() else 0,
            ):
                await get_user(conn, 1)
        except asyncio.TimeoutError:
            pass
        timings.append((time.perf_counter() - start) * 1000)

    slow = [t for t in timings if t > 100]
    fast = [t for t in timings if t <= 100]
    print(f"  slow queries (>100ms): {len(slow)}  fast (<100ms): {len(fast)}")
    print(f"  max: {max(timings):.0f}ms  avg: {sum(timings)/len(timings):.0f}ms")


async def main() -> None:
    await experiment_slow_query_timeout()
    await experiment_query_within_sla()
    await experiment_connection_loss_and_reconnect()
    await experiment_concurrent_queries_under_load()
    await experiment_partial_degradation()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
