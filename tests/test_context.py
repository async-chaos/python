from __future__ import annotations

import asyncio
import time

import pytest

from asynchaos import chaos_zone, drop_connections, inject_latency
from asynchaos.context import _CHAOS_ZONE_VAR


# ---------------------------------------------------------------------------
# chaos_zone: latency override
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("zone_ms,decorator_ms,expected_ms", [
    pytest.param(150, 10, 150,
                 id="chaos_zone(latency=150ms) overrides decorator(10ms) → ~150ms"),
    pytest.param(80, 10, 80,
                 id="chaos_zone(latency=80ms) overrides decorator(10ms) → ~80ms"),
])
async def test_chaos_zone_latency_override(zone_ms, decorator_ms, expected_ms):
    @inject_latency(min_ms=decorator_ms, max_ms=decorator_ms, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency=zone_ms):
        start = time.perf_counter()
        await fn()
        elapsed = time.perf_counter() - start

    lo, hi = expected_ms * 0.8 / 1000, expected_ms * 2.0 / 1000
    assert lo <= elapsed <= hi, f"Expected ~{expected_ms}ms from zone, got {elapsed*1000:.0f}ms"


async def test_chaos_zone_restores_decorator_latency_after_exit():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency=500):
        pass  # enter and immediately exit

    start = time.perf_counter()
    await fn()
    assert (time.perf_counter() - start) < 0.05, "Zone should be gone; decorator default (10ms) should apply"


# ---------------------------------------------------------------------------
# chaos_zone: ContextVar invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", [
    pytest.param("outside",
                 id="chaos_zone ContextVar is None outside any zone"),
    pytest.param("inside",
                 id="chaos_zone ContextVar is set inside zone"),
])
async def test_chaos_zone_contextvar_state(scenario):
    if scenario == "outside":
        assert _CHAOS_ZONE_VAR.get() is None
    else:
        async with chaos_zone(latency=50):
            assert _CHAOS_ZONE_VAR.get() is not None
        assert _CHAOS_ZONE_VAR.get() is None


@pytest.mark.parametrize("raises_in_body", [
    pytest.param(False,
                 id="chaos_zone restores ContextVar on clean exit"),
    pytest.param(True,
                 id="chaos_zone restores ContextVar even when body raises"),
])
async def test_chaos_zone_contextvar_always_restored(raises_in_body):
    before = _CHAOS_ZONE_VAR.get()

    try:
        async with chaos_zone(latency=500):
            if raises_in_body:
                raise RuntimeError("error inside zone")
    except RuntimeError:
        pass

    assert _CHAOS_ZONE_VAR.get() == before


# ---------------------------------------------------------------------------
# chaos_zone: nesting
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outer_ms,inner_ms", [
    pytest.param(50, 100,
                 id="nested zones: inner(100ms) overrides outer(50ms); outer restored on inner exit"),
])
async def test_chaos_zone_nesting(outer_ms, inner_ms):
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency=outer_ms):
        async with chaos_zone(latency=inner_ms):
            start = time.perf_counter()
            await fn()
            inner_elapsed = time.perf_counter() - start

        start = time.perf_counter()
        await fn()
        outer_elapsed = time.perf_counter() - start

    assert inner_elapsed >= inner_ms * 0.8 / 1000, f"Inner zone ~{inner_ms}ms, got {inner_elapsed*1000:.0f}ms"
    assert outer_elapsed >= outer_ms * 0.8 / 1000, f"Outer zone ~{outer_ms}ms, got {outer_elapsed*1000:.0f}ms"
    assert outer_elapsed < inner_ms * 0.8 / 1000, "Outer zone must be shorter than inner zone"


async def test_chaos_zone_nested_exception_restores_outer():
    async with chaos_zone(latency=50):
        outer_config = _CHAOS_ZONE_VAR.get()
        try:
            async with chaos_zone(latency=200):
                raise ValueError("inner exception")
        except ValueError:
            pass
        assert _CHAOS_ZONE_VAR.get() is outer_config, "Outer zone config must be restored after inner raises"


# ---------------------------------------------------------------------------
# chaos_zone: task propagation (ContextVar snapshot-at-creation semantics)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("create_inside_zone,expect_zone_latency", [
    pytest.param(True, True,
                 id="create_task() inside zone → task snapshot includes zone config"),
    pytest.param(False, False,
                 id="create_task() before zone → task snapshot excludes zone config"),
])
async def test_chaos_zone_task_propagation(create_inside_zone, expect_zone_latency):
    zone_latency_ms = 300

    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    if create_inside_zone:
        async with chaos_zone(latency=zone_latency_ms):
            start = time.perf_counter()
            task = asyncio.create_task(fn())
            await task
            elapsed = time.perf_counter() - start
    else:
        task = asyncio.create_task(fn())
        async with chaos_zone(latency=zone_latency_ms):
            start = time.perf_counter()
            await task
            elapsed = time.perf_counter() - start

    if expect_zone_latency:
        assert elapsed >= zone_latency_ms * 0.8 / 1000, \
            f"Task inside zone should get {zone_latency_ms}ms, got {elapsed*1000:.0f}ms"
    else:
        assert elapsed < zone_latency_ms * 0.5 / 1000, \
            f"Task outside zone should not get zone latency, got {elapsed*1000:.0f}ms"


async def test_chaos_zone_propagates_to_gathered_coroutines():
    @inject_latency(min_ms=10, max_ms=10, probability=1.0)
    async def fn():
        return True

    async with chaos_zone(latency=80):
        start = time.perf_counter()
        results = await asyncio.gather(fn(), fn(), fn())
        elapsed = time.perf_counter() - start

    assert all(results)
    assert elapsed >= 0.06, f"Gathered coroutines should see zone latency, got {elapsed*1000:.0f}ms"


# ---------------------------------------------------------------------------
# chaos_zone: drop override
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("zone_drop_rate,decorator_prob,expected", [
    pytest.param(1.0, 0.0, "raises",
                 id="chaos_zone(drop_rate=1.0) overrides decorator(prob=0.0) → always drops"),
    pytest.param(None, 0.0, "ok",
                 id="no zone drop → decorator(prob=0.0) → never drops"),
])
async def test_chaos_zone_drop_override(zone_drop_rate, decorator_prob, expected):
    @drop_connections(probability=decorator_prob)
    async def fn():
        return "ok"

    zone_kwargs = {}
    if zone_drop_rate is not None:
        zone_kwargs["drop_rate"] = zone_drop_rate

    async with chaos_zone(**zone_kwargs):
        if expected == "raises":
            with pytest.raises(ConnectionError):
                await fn()
        else:
            assert await fn() == "ok"


async def test_chaos_zone_drop_restores_after_exit():
    @drop_connections(probability=0.0)
    async def fn():
        return "ok"

    async with chaos_zone(drop_rate=1.0):
        pass

    assert await fn() == "ok", "Zone drop must not persist after exit"


# ---------------------------------------------------------------------------
# _ChaosCtxProxy: manual injection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("has_latency,has_drop,action,expect_delay,expect_raise", [
    pytest.param(True, False, "inject_latency", True, False,
                 id="ctx.inject_latency() with zone latency=60ms → sleeps ~60ms"),
    pytest.param(False, False, "inject_latency", False, False,
                 id="ctx.inject_latency() with no latency configured → no-op"),
    pytest.param(False, True, "maybe_drop", False, True,
                 id="ctx.maybe_drop() with drop_rate=1.0 → ConnectionError"),
    pytest.param(False, False, "maybe_drop", False, False,
                 id="ctx.maybe_drop() with no drop configured → no-op"),
])
async def test_ctx_proxy(has_latency, has_drop, action, expect_delay, expect_raise):
    zone_kwargs = {}
    if has_latency:
        zone_kwargs["latency"] = 60
    if has_drop:
        zone_kwargs["drop_rate"] = 1.0

    async with chaos_zone(**zone_kwargs) as ctx:
        if action == "inject_latency":
            start = time.perf_counter()
            await ctx.inject_latency()
            elapsed = time.perf_counter() - start
            if expect_delay:
                assert elapsed >= 0.04, f"Expected ~60ms, got {elapsed*1000:.0f}ms"
            else:
                assert elapsed < 0.02, f"Expected no-op, got {elapsed*1000:.0f}ms"
        else:
            if expect_raise:
                with pytest.raises(ConnectionError):
                    ctx.maybe_drop()
            else:
                ctx.maybe_drop()  # must not raise
