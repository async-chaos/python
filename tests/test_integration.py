from __future__ import annotations

import asyncio
import time

import pytest

from asynchaos import chaos_zone, drop_connections, inject_latency
from asynchaos.patch import chaos_patch


# ---------------------------------------------------------------------------
# Mock async HTTP client
# ---------------------------------------------------------------------------

class MockHttpClient:
    async def get(self, url: str) -> str:
        return f"200 OK: {url}"

    async def post(self, url: str, data=None) -> str:
        return f"201 Created: {url}"


# ---------------------------------------------------------------------------
# chaos_patch — basic behaviour
# ---------------------------------------------------------------------------


async def test_chaos_patch_injects_latency():
    with chaos_patch(MockHttpClient, ["get"], latency_min_ms=50, latency_max_ms=50):
        c = MockHttpClient()
        start = time.perf_counter()
        result = await c.get("http://test.com")
        elapsed = time.perf_counter() - start

    assert result == "200 OK: http://test.com"
    assert 0.04 <= elapsed <= 0.10, f"Expected ~50ms, got {elapsed:.3f}s"


async def test_chaos_patch_restores_after_exit():
    with chaos_patch(MockHttpClient, ["get"], latency_min_ms=5000, latency_max_ms=5000):
        pass  # enter and exit immediately

    c = MockHttpClient()
    start = time.perf_counter()
    await c.get("http://test.com")
    assert (time.perf_counter() - start) < 0.05, "Patch must be removed after exit"


async def test_chaos_patch_restores_on_exception():
    try:
        with chaos_patch(MockHttpClient, ["get"], latency_min_ms=5000, latency_max_ms=5000):
            raise RuntimeError("error inside patch block")
    except RuntimeError:
        pass

    c = MockHttpClient()
    start = time.perf_counter()
    await c.get("http://test.com")
    assert (time.perf_counter() - start) < 0.05, "Patch must be removed even after exception"


async def test_chaos_patch_drop_rate_always():
    with chaos_patch(MockHttpClient, ["get"], drop_rate=1.0):
        c = MockHttpClient()
        with pytest.raises(ConnectionError):
            await c.get("http://test.com")


async def test_chaos_patch_drop_rate_never():
    with chaos_patch(MockHttpClient, ["get"], drop_rate=0.0):
        c = MockHttpClient()
        result = await c.get("http://test.com")
    assert result == "200 OK: http://test.com"


async def test_chaos_patch_multiple_methods():
    with chaos_patch(MockHttpClient, ["get", "post"], latency_min_ms=30, latency_max_ms=30):
        c = MockHttpClient()
        start = time.perf_counter()
        await c.get("http://a.com")
        await c.post("http://a.com")
        elapsed = time.perf_counter() - start

    assert elapsed >= 0.05, f"Both methods should be delayed, got {elapsed:.3f}s"


async def test_chaos_patch_rejects_sync_method():
    class SyncClient:
        def get(self, url: str) -> str:
            return url

    with pytest.raises(TypeError, match="not a coroutine function"):
        with chaos_patch(SyncClient, ["get"], latency_min_ms=10):
            pass


async def test_chaos_patch_latency_shorthand():
    with chaos_patch(MockHttpClient, ["get"], latency=50):
        c = MockHttpClient()
        start = time.perf_counter()
        await c.get("http://test.com")
        elapsed = time.perf_counter() - start
    assert 0.04 <= elapsed <= 0.10


# ---------------------------------------------------------------------------
# chaos_zone + chaos_patch interaction
# ---------------------------------------------------------------------------


async def test_chaos_patch_respects_chaos_zone():
    """chaos_zone latency overrides chaos_patch's latency."""
    with chaos_patch(MockHttpClient, ["get"], latency_min_ms=10, latency_max_ms=10):
        c = MockHttpClient()
        async with chaos_zone(latency_min_ms=80, latency_max_ms=80):
            start = time.perf_counter()
            await c.get("http://test.com")
            elapsed = time.perf_counter() - start

    assert 0.07 <= elapsed <= 0.13, f"Zone should override patch latency, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Concurrent task interactions
# ---------------------------------------------------------------------------


async def test_zone_with_concurrent_tasks():
    """All coroutines gathered inside a zone see the zone config."""

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=70, latency_max_ms=70):
        start = time.perf_counter()
        results = await asyncio.gather(*[fn() for _ in range(4)])
        elapsed = time.perf_counter() - start

    assert all(results), "All calls should succeed"
    # Each fn() sleeps ~70ms; with gather they run concurrently so total ~70ms
    assert elapsed >= 0.06, f"Expected >=60ms (zone latency), got {elapsed:.3f}s"


async def test_zone_does_not_leak_to_tasks_created_before():
    """A task created BEFORE zone entry must not inherit zone config."""

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return time.perf_counter()

    # Create task BEFORE zone — it snapshots context without zone
    task_outside = asyncio.create_task(fn())

    async with chaos_zone(latency_min_ms=500, latency_max_ms=500):
        task_inside = asyncio.create_task(fn())
        inside_start = time.perf_counter()
        inside_t = await task_inside
        inside_elapsed = inside_t - inside_start

    outside_start = time.perf_counter()
    outside_t = await task_outside
    outside_elapsed = outside_t - outside_start

    assert inside_elapsed >= 0.4, f"Inside task should get zone latency, got {inside_elapsed:.3f}s"
    assert outside_elapsed < 0.1, f"Outside task should not get zone latency, got {outside_elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Global disable interacts with patch
# ---------------------------------------------------------------------------


async def test_chaos_patch_respects_global_disable():
    import asynchaos

    asynchaos.disable()
    try:
        with chaos_patch(MockHttpClient, ["get"], latency_min_ms=5000, latency_max_ms=5000):
            c = MockHttpClient()
            start = time.perf_counter()
            await c.get("http://test.com")
            assert (time.perf_counter() - start) < 0.05, "Global disable must bypass patch"
    finally:
        asynchaos.enable()
