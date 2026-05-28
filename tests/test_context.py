from __future__ import annotations

import asyncio
import time

import pytest

from asynchaos import chaos_zone, drop_connections, inject_latency
from asynchaos.context import _CHAOS_ZONE_VAR


# ---------------------------------------------------------------------------
# chaos_zone — latency override
# ---------------------------------------------------------------------------


async def test_zone_overrides_decorator_latency():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=80, latency_max_ms=80):
        start = time.perf_counter()
        await fn()
        elapsed = time.perf_counter() - start

    assert 0.065 <= elapsed <= 0.150, f"Expected ~80ms from zone, got {elapsed:.3f}s"


async def test_zone_restores_decorator_latency_after_exit():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=500, latency_max_ms=500):
        pass  # enter and immediately exit

    start = time.perf_counter()
    await fn()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"Zone should be gone; expected ~10ms, got {elapsed:.3f}s"


async def test_zone_latency_shorthand():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency=80):
        start = time.perf_counter()
        await fn()
        elapsed = time.perf_counter() - start

    assert 0.065 <= elapsed <= 0.150


# ---------------------------------------------------------------------------
# chaos_zone — nesting
# ---------------------------------------------------------------------------


async def test_nested_zones_inner_overrides_outer():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=50, latency_max_ms=50):
        async with chaos_zone(latency_min_ms=100, latency_max_ms=100):
            start = time.perf_counter()
            await fn()
            inner_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        await fn()
        outer_elapsed = time.perf_counter() - start

    assert 0.08 <= inner_elapsed <= 0.15, f"Inner zone should be ~100ms, got {inner_elapsed:.3f}s"
    assert 0.03 <= outer_elapsed <= 0.08, f"Outer zone should be ~50ms, got {outer_elapsed:.3f}s"


async def test_nested_zones_restore_to_none_after_both_exit():
    async with chaos_zone(latency=50):
        async with chaos_zone(latency=100):
            pass  # inner exits
        # still in outer zone
        assert _CHAOS_ZONE_VAR.get() is not None

    # both exited
    assert _CHAOS_ZONE_VAR.get() is None


# ---------------------------------------------------------------------------
# chaos_zone — ContextVar invariants
# ---------------------------------------------------------------------------


async def test_zone_var_is_none_outside():
    assert _CHAOS_ZONE_VAR.get() is None


async def test_zone_var_is_set_inside():
    async with chaos_zone(latency=50):
        assert _CHAOS_ZONE_VAR.get() is not None


async def test_zone_restores_on_exception():
    before = _CHAOS_ZONE_VAR.get()
    try:
        async with chaos_zone(latency_min_ms=500, latency_max_ms=500):
            raise RuntimeError("chaos in the chaos zone")
    except RuntimeError:
        pass

    after = _CHAOS_ZONE_VAR.get()
    assert after == before, "ContextVar must be restored after exception in body"


async def test_zone_restores_on_nested_exception():
    async with chaos_zone(latency=50):
        outer_config = _CHAOS_ZONE_VAR.get()
        try:
            async with chaos_zone(latency=200):
                raise ValueError("inner exception")
        except ValueError:
            pass
        # back to outer zone
        assert _CHAOS_ZONE_VAR.get() is outer_config


# ---------------------------------------------------------------------------
# chaos_zone — task propagation
# ---------------------------------------------------------------------------


async def test_zone_propagates_into_subtask():
    """Tasks created INSIDE the zone inherit the zone config."""

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=80, latency_max_ms=80):
        start = time.perf_counter()
        task = asyncio.create_task(fn())
        await task
        elapsed = time.perf_counter() - start

    assert elapsed >= 0.065, f"Task should see zone latency, got {elapsed:.3f}s"


async def test_zone_does_not_propagate_to_task_created_before():
    """Tasks created BEFORE zone entry must NOT inherit zone config."""

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    task_before = asyncio.create_task(fn())

    async with chaos_zone(latency_min_ms=500, latency_max_ms=500):
        task_inside = asyncio.create_task(fn())
        inside_start = time.perf_counter()
        await task_inside
        inside_elapsed = time.perf_counter() - inside_start

    outside_start = time.perf_counter()
    await task_before
    outside_elapsed = time.perf_counter() - outside_start

    assert inside_elapsed >= 0.4, f"Inside task should get zone latency, got {inside_elapsed:.3f}s"
    assert outside_elapsed < 0.1, f"Outside task should not get zone latency, got {outside_elapsed:.3f}s"


async def test_zone_propagates_to_gathered_coroutines():
    """asyncio.gather creates tasks at call time — all should see the zone."""

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency_min_ms=60, latency_max_ms=60):
        start = time.perf_counter()
        results = await asyncio.gather(fn(), fn(), fn())
        elapsed = time.perf_counter() - start

    assert all(results)
    assert elapsed >= 0.05, f"Gathered coroutines should see zone latency, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# chaos_zone — drop override
# ---------------------------------------------------------------------------


async def test_zone_overrides_drop_condition():
    @drop_connections(probability=0.0)
    async def fn():
        return "ok"

    async with chaos_zone(drop_rate=1.0):
        with pytest.raises(ConnectionError):
            await fn()


async def test_zone_drop_restores_after_exit():
    @drop_connections(probability=0.0)
    async def fn():
        return "ok"

    async with chaos_zone(drop_rate=1.0):
        pass

    result = await fn()
    assert result == "ok"


# ---------------------------------------------------------------------------
# _ChaosCtxProxy manual injection
# ---------------------------------------------------------------------------


async def test_ctx_proxy_inject_latency():
    async with chaos_zone(latency_min_ms=60, latency_max_ms=60) as ctx:
        start = time.perf_counter()
        await ctx.inject_latency()
        elapsed = time.perf_counter() - start

    assert 0.05 <= elapsed <= 0.10, f"Expected ~60ms from manual inject, got {elapsed:.3f}s"


async def test_ctx_proxy_inject_latency_no_op_without_latency():
    async with chaos_zone(drop_rate=0.5) as ctx:
        start = time.perf_counter()
        await ctx.inject_latency()  # no latency configured in zone
        elapsed = time.perf_counter() - start

    assert elapsed < 0.02, f"Should be a no-op, got {elapsed:.3f}s"


async def test_ctx_proxy_maybe_drop_triggers():
    async with chaos_zone(drop_rate=1.0) as ctx:
        with pytest.raises(ConnectionError):
            ctx.maybe_drop()


async def test_ctx_proxy_maybe_drop_no_op_without_drop():
    async with chaos_zone(latency=10) as ctx:
        ctx.maybe_drop()  # should not raise
