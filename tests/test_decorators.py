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

@pytest.mark.parametrize("min_ms,max_ms,prob,expect_delay", [
    pytest.param(80, 80, 1.0, True,
                 id="inject_latency(80ms, prob=1.0) → always sleeps ~80ms"),
    pytest.param(5000, 5000, 0.0, False,
                 id="inject_latency(5000ms, prob=0.0) → never sleeps"),
])
async def test_inject_latency_timing(min_ms, max_ms, prob, expect_delay):
    @inject_latency(min_ms=min_ms, max_ms=max_ms, probability=prob)
    async def fn():
        return True

    start = time.perf_counter()
    await fn()
    elapsed = time.perf_counter() - start

    if expect_delay:
        assert elapsed >= (min_ms * 0.8) / 1000, f"Expected ~{min_ms}ms delay, got {elapsed*1000:.0f}ms"
    else:
        assert elapsed < 0.05, f"Expected no delay, got {elapsed*1000:.0f}ms"


@pytest.mark.parametrize("min_ms,max_ms", [
    pytest.param(1, 1, id="inject_latency(1ms) → preserves return value and kwargs"),
])
async def test_inject_latency_transparent(min_ms, max_ms):
    @inject_latency(min_ms=min_ms, max_ms=max_ms)
    async def fn(x, *, label):
        return f"{label}:{x}"

    assert await fn(42, label="result") == "result:42"


async def test_inject_latency_rejects_sync_function():
    with pytest.raises(TypeError, match="async def"):
        @inject_latency(min_ms=100)
        def sync_fn():
            pass


async def test_inject_latency_propagates_downstream_exceptions():
    @inject_latency(min_ms=1, max_ms=1)
    async def fn():
        raise ValueError("downstream error")

    with pytest.raises(ValueError, match="downstream error"):
        await fn()


@pytest.mark.parametrize("fail_count,window,call_count,expected_delayed", [
    pytest.param(1, 2, 4, [True, False, True, False],
                 id="inject_latency(RateCondition(1/2)) → every other call delayed"),
    pytest.param(2, 5, 10, [True, True, False, False, False, True, True, False, False, False],
                 id="inject_latency(RateCondition(2/5)) → first 2 of each 5 delayed"),
])
async def test_inject_latency_rate_condition(fail_count, window, call_count, expected_delayed):
    rc = RateCondition(fail_count=fail_count, window=window)

    @inject_latency(min_ms=80, max_ms=80, probability=rc)
    async def fn():
        return True

    results = []
    for _ in range(call_count):
        start = time.perf_counter()
        await fn()
        results.append((time.perf_counter() - start) >= 0.06)

    assert results == expected_delayed


@pytest.mark.parametrize("control,expected_fast", [
    pytest.param("disable", True,
                 id="inject_latency(5s) + asynchaos.disable() → no sleep"),
    pytest.param("global_prob_zero", True,
                 id="inject_latency(5s) + global_probability=0.0 → no sleep"),
])
async def test_inject_latency_global_control(control, expected_fast):
    @inject_latency(min_ms=5000, max_ms=5000, probability=1.0)
    async def fn():
        return True

    if control == "disable":
        asynchaos.disable()
    else:
        asynchaos.configure(global_probability=0.0)

    start = time.perf_counter()
    await fn()
    assert (time.perf_counter() - start) < 0.05


# ---------------------------------------------------------------------------
# @drop_connections
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("probability,exception_cls,should_raise", [
    pytest.param(1.0, ConnectionError, True,
                 id="drop_connections(prob=1.0) → always raises ConnectionError"),
    pytest.param(0.0, ConnectionError, False,
                 id="drop_connections(prob=0.0) → never raises, fn executes normally"),
    pytest.param(1.0, OSError, True,
                 id="drop_connections(prob=1.0, exception=OSError) → always raises OSError"),
])
async def test_drop_connections_raises(probability, exception_cls, should_raise):
    @drop_connections(probability=probability, exception=exception_cls)
    async def fn():
        return "ok"

    if should_raise:
        with pytest.raises(exception_cls):
            await fn()
    else:
        assert await fn() == "ok"


async def test_drop_connections_body_never_runs_on_drop():
    called = []

    @drop_connections(probability=1.0)
    async def fn():
        called.append(True)

    with pytest.raises(ConnectionError):
        await fn()

    assert called == [], "fn body must not execute when drop fires"


async def test_drop_connections_statistical_rate():
    @drop_connections(probability=0.5)
    async def fn():
        return True

    drops = 0
    for _ in range(1000):
        if not await _try(fn()):
            drops += 1
    assert 350 < drops < 650, f"Expected ~500 drops at prob=0.5, got {drops}"


@pytest.mark.parametrize("exception_cls,is_chaos_exception", [
    pytest.param(
        "ConnectionDropped", True,
        id="drop_connections(exception=ConnectionDropped) → ChaosException subclass catchable as ConnectionError",
    ),
])
async def test_drop_connections_chaos_exception_hierarchy(exception_cls, is_chaos_exception):
    from asynchaos.exceptions import ConnectionDropped

    @drop_connections(probability=1.0, exception=ConnectionDropped)
    async def fn():
        return "ok"

    with pytest.raises(ConnectionDropped) as exc_info:
        await fn()

    assert isinstance(exc_info.value, ConnectionError)
    assert isinstance(exc_info.value, ChaosException)


async def test_drop_connections_disabled_globally():
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


# ---------------------------------------------------------------------------
# @timeout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("deadline_s,call_duration_s,should_timeout,exception_cls", [
    pytest.param(0.05, 10.0, True, asyncio.TimeoutError,
                 id="timeout(0.05s) → fires, call would take 10s"),
    pytest.param(1.0, 0.0, False, None,
                 id="timeout(1.0s) → passes, fast call completes normally"),
    pytest.param(0.05, 10.0, True, RuntimeError,
                 id="timeout(0.05s, exception=RuntimeError) → custom exception raised"),
])
async def test_timeout_deadline(deadline_s, call_duration_s, should_timeout, exception_cls):
    exc_type = exception_cls or asyncio.TimeoutError

    @timeout(seconds=deadline_s, exception=exc_type)
    async def fn():
        if call_duration_s > 0:
            await asyncio.sleep(call_duration_s)
        return "ok"

    if should_timeout:
        with pytest.raises(exc_type):
            await fn()
    else:
        assert await fn() == "ok"


@pytest.mark.parametrize("seconds,error_match", [
    pytest.param(0, "must be > 0", id="timeout(seconds=0) → ValueError at decoration time"),
    pytest.param(-1, "must be > 0", id="timeout(seconds=-1) → ValueError at decoration time"),
])
def test_timeout_invalid_deadline(seconds, error_match):
    with pytest.raises(ValueError, match=error_match):
        @timeout(seconds=seconds)
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

    assert await slow() == "ok"


# ---------------------------------------------------------------------------
# @chaos (combined)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("latency_ms,timeout_s,drop_rate,expected_outcome", [
    pytest.param(500, 0.05, None, "timeout",
                 id="chaos(latency=500ms, timeout=0.05s) → timeout fires during latency sleep"),
    pytest.param(20, 1.0, None, "ok",
                 id="chaos(latency=20ms, timeout=1.0s) → latency within budget, call succeeds"),
    pytest.param(None, None, 1.0, "drop",
                 id="chaos(drop_rate=1.0) → ConnectionError before fn body runs"),
    pytest.param(500, 0.05, 1.0, "timeout",
                 id="chaos(latency=500ms, timeout=0.05s, drop_rate=1.0) → timeout wins over drop"),
])
async def test_chaos_combined(latency_ms, timeout_s, drop_rate, expected_outcome):
    called = []

    kwargs = {}
    if latency_ms is not None:
        kwargs["latency"] = latency_ms
    if timeout_s is not None:
        kwargs["timeout_seconds"] = timeout_s
    if drop_rate is not None:
        kwargs["drop_rate"] = drop_rate

    @chaos(**kwargs)
    async def fn():
        called.append(True)
        return "ok"

    if expected_outcome == "timeout":
        with pytest.raises(asyncio.TimeoutError):
            await fn()
    elif expected_outcome == "drop":
        with pytest.raises(ConnectionError):
            await fn()
        assert called == [], "fn body must not run when drop fires"
    else:
        result = await fn()
        assert result == "ok"


@pytest.mark.parametrize("exception_cls,triggered_by", [
    pytest.param(RuntimeError, "timeout",
                 id="chaos(timeout, custom_exception=RuntimeError) → RuntimeError on deadline"),
    pytest.param(OSError, "drop",
                 id="chaos(drop, custom_exception=OSError) → OSError on connection drop"),
])
async def test_chaos_custom_exceptions(exception_cls, triggered_by):
    if triggered_by == "timeout":
        @chaos(latency=500, timeout_seconds=0.05, timeout_exception=exception_cls)
        async def fn():
            return "ok"
    else:
        @chaos(drop_rate=1.0, drop_exception=exception_cls)
        async def fn():
            return "ok"

    with pytest.raises(exception_cls):
        await fn()


async def test_chaos_global_disable_bypasses_all():
    asynchaos.disable()

    @chaos(latency=5000, drop_rate=1.0, timeout_seconds=0.001)
    async def fn():
        return "ok"

    assert await fn() == "ok"


async def test_chaos_rejects_sync():
    with pytest.raises(TypeError, match="async def"):
        @chaos(latency=100)
        def sync_fn():
            pass


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

async def _try(coro) -> bool:
    try:
        await coro
        return True
    except Exception:
        return False
