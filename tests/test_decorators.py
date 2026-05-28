from __future__ import annotations

import asyncio
import time

import pytest

import asynchaos
from asynchaos import chaos, drop_connections, inject_latency, timeout
from asynchaos.conditions import RateCondition
from asynchaos.exceptions import ChaosException


# ---------------------------------------------------------------------------
# @inject_latency
# ---------------------------------------------------------------------------


async def test_inject_latency_adds_delay():
    @inject_latency(min_ms=80, max_ms=80, probability=1.0)
    async def fn():
        return 42

    start = time.perf_counter()
    result = await fn()
    elapsed = time.perf_counter() - start

    assert result == 42
    assert 0.065 <= elapsed <= 0.150, f"Expected ~80ms, got {elapsed:.3f}s"


async def test_inject_latency_probability_zero_skips():
    @inject_latency(min_ms=5000, max_ms=5000, probability=0.0)
    async def fn():
        return 1

    start = time.perf_counter()
    await fn()
    assert (time.perf_counter() - start) < 0.05


async def test_inject_latency_preserves_return_value():
    @inject_latency(min_ms=1, max_ms=1)
    async def fn(x, y):
        return x + y

    assert await fn(3, 4) == 7


async def test_inject_latency_preserves_kwargs():
    @inject_latency(min_ms=1, max_ms=1)
    async def fn(*, value):
        return value

    assert await fn(value="hello") == "hello"


async def test_inject_latency_rejects_sync_function():
    with pytest.raises(TypeError, match="async def"):

        @inject_latency(min_ms=100)
        def sync_fn():
            pass


async def test_inject_latency_propagates_exceptions():
    @inject_latency(min_ms=1, max_ms=1)
    async def fn():
        raise ValueError("downstream error")

    with pytest.raises(ValueError, match="downstream error"):
        await fn()


async def test_inject_latency_rate_condition():
    """RateCondition(fail_count=1, window=2): every other call should be delayed."""
    rc = RateCondition(fail_count=1, window=2)

    @inject_latency(min_ms=80, max_ms=80, probability=rc)
    async def fn():
        return True

    timings = []
    for _ in range(4):
        start = time.perf_counter()
        await fn()
        timings.append(time.perf_counter() - start)

    # Calls 1, 3 are delayed; calls 2, 4 are not
    assert timings[0] >= 0.06, f"Call 1 should be delayed, got {timings[0]:.3f}s"
    assert timings[1] < 0.05, f"Call 2 should not be delayed, got {timings[1]:.3f}s"
    assert timings[2] >= 0.06, f"Call 3 should be delayed, got {timings[2]:.3f}s"
    assert timings[3] < 0.05, f"Call 4 should not be delayed, got {timings[3]:.3f}s"


async def test_inject_latency_global_disable():
    asynchaos.disable()

    @inject_latency(min_ms=5000, max_ms=5000, probability=1.0)
    async def fn():
        return 1

    start = time.perf_counter()
    await fn()
    assert (time.perf_counter() - start) < 0.05


async def test_inject_latency_global_probability_scales():
    asynchaos.configure(global_probability=0.0)

    @inject_latency(min_ms=5000, max_ms=5000, probability=1.0)
    async def fn():
        return 1

    start = time.perf_counter()
    await fn()
    assert (time.perf_counter() - start) < 0.05


# ---------------------------------------------------------------------------
# @drop_connections
# ---------------------------------------------------------------------------


async def test_drop_connections_always_drops():
    @drop_connections(probability=1.0)
    async def fn():
        return "ok"

    with pytest.raises(ConnectionError):
        await fn()


async def test_drop_connections_never_drops():
    @drop_connections(probability=0.0)
    async def fn():
        return "ok"

    assert await fn() == "ok"


async def test_drop_connections_custom_exception():
    class MyError(Exception):
        pass

    @drop_connections(probability=1.0, exception=MyError)
    async def fn():
        return "ok"

    with pytest.raises(MyError):
        await fn()


async def test_drop_connections_body_not_called_on_drop():
    called = []

    @drop_connections(probability=1.0)
    async def fn():
        called.append(True)

    with pytest.raises(ConnectionError):
        await fn()

    assert called == [], "Function body must not be called when drop fires"


async def test_drop_connections_statistical_rate():
    """Over 1000 calls with probability=0.5, expect 350–650 drops."""
    count = 0

    @drop_connections(probability=0.5)
    async def fn():
        return True

    for _ in range(1000):
        try:
            await fn()
        except ConnectionError:
            count += 1

    assert 350 < count < 650, f"Expected ~500 drops, got {count}"


async def test_drop_connections_global_disable():
    asynchaos.disable()

    @drop_connections(probability=1.0)
    async def fn():
        return "ok"

    assert await fn() == "ok"


async def test_drop_connections_rejects_sync():
    with pytest.raises(TypeError, match="async def"):

        @drop_connections(probability=0.1)
        def sync_fn():
            pass


async def test_drop_connections_chaos_exception_subclass():
    from asynchaos.exceptions import ConnectionDropped

    @drop_connections(probability=1.0, exception=ConnectionDropped)
    async def fn():
        return "ok"

    with pytest.raises(ConnectionDropped) as exc_info:
        await fn()

    # Must also be catchable as ConnectionError and ChaosException
    assert isinstance(exc_info.value, ConnectionError)
    assert isinstance(exc_info.value, ChaosException)


# ---------------------------------------------------------------------------
# @timeout
# ---------------------------------------------------------------------------


async def test_timeout_fires():
    @timeout(seconds=0.05)
    async def slow():
        await asyncio.sleep(10)
        return "never"

    with pytest.raises(asyncio.TimeoutError):
        await slow()


async def test_timeout_passes_fast_calls():
    @timeout(seconds=1.0)
    async def fast():
        return "ok"

    assert await fast() == "ok"


async def test_timeout_custom_exception():
    class ServiceTimeout(Exception):
        pass

    @timeout(seconds=0.05, exception=ServiceTimeout)
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(ServiceTimeout):
        await slow()


async def test_timeout_rejects_zero_seconds():
    with pytest.raises(ValueError):

        @timeout(seconds=0)
        async def fn():
            pass


async def test_timeout_rejects_negative_seconds():
    with pytest.raises(ValueError):

        @timeout(seconds=-1.0)
        async def fn():
            pass


async def test_timeout_rejects_sync():
    with pytest.raises(TypeError, match="async def"):

        @timeout(seconds=1.0)
        def sync_fn():
            pass


async def test_timeout_global_disable_bypasses():
    asynchaos.disable()

    @timeout(seconds=0.001)
    async def slow():
        await asyncio.sleep(0.1)
        return "ok"

    result = await slow()
    assert result == "ok"


# ---------------------------------------------------------------------------
# @chaos combined
# ---------------------------------------------------------------------------


async def test_chaos_latency_then_timeout():
    """latency (500ms) > timeout (50ms) → timeout fires."""

    @chaos(latency=500, timeout_seconds=0.05)
    async def fn():
        return "never"

    with pytest.raises(asyncio.TimeoutError):
        await fn()


async def test_chaos_latency_within_timeout():
    @chaos(latency=20, timeout_seconds=1.0)
    async def fn():
        return "ok"

    start = time.perf_counter()
    result = await fn()
    elapsed = time.perf_counter() - start

    assert result == "ok"
    assert 0.01 <= elapsed <= 0.1


async def test_chaos_drop_fires_before_body():
    called = []

    @chaos(drop_rate=1.0)
    async def fn():
        called.append(True)

    with pytest.raises(ConnectionError):
        await fn()

    assert called == [], "Function body must not run when drop fires"


async def test_chaos_latency_shorthand():
    @chaos(latency=50)
    async def fn():
        return True

    start = time.perf_counter()
    await fn()
    elapsed = time.perf_counter() - start
    assert 0.04 <= elapsed <= 0.10


async def test_chaos_only_timeout():
    @chaos(timeout_seconds=0.05)
    async def slow():
        await asyncio.sleep(10)

    with pytest.raises(asyncio.TimeoutError):
        await slow()


async def test_chaos_only_drop():
    @chaos(drop_rate=1.0)
    async def fn():
        return "ok"

    with pytest.raises(ConnectionError):
        await fn()


async def test_chaos_custom_timeout_exception():
    class DBTimeout(Exception):
        pass

    @chaos(latency=500, timeout_seconds=0.05, timeout_exception=DBTimeout)
    async def fn():
        return "never"

    with pytest.raises(DBTimeout):
        await fn()


async def test_chaos_custom_drop_exception():
    class NetworkError(Exception):
        pass

    @chaos(drop_rate=1.0, drop_exception=NetworkError)
    async def fn():
        return "ok"

    with pytest.raises(NetworkError):
        await fn()


async def test_chaos_global_disable():
    asynchaos.disable()

    @chaos(latency=5000, drop_rate=1.0, timeout_seconds=0.001)
    async def fn():
        return "ok"

    result = await fn()
    assert result == "ok"


async def test_chaos_rejects_sync():
    with pytest.raises(TypeError, match="async def"):

        @chaos(latency=100)
        def sync_fn():
            pass
