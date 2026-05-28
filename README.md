# asynchaos

[![CI](https://github.com/async-chaos/asynchaos-python/actions/workflows/ci.yml/badge.svg)](https://github.com/async-chaos/asynchaos-python/actions/workflows/ci.yml)
[![PyPI version](https://badge.fury.io/py/asynchaos.svg)](https://badge.fury.io/py/asynchaos)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

**Lightweight async chaos engineering for Python.** Inject latency, connection drops, and timeouts into your async services during integration testing — with a single decorator.

Zero runtime dependencies. Works with any asyncio-based framework (FastAPI, aiohttp, asyncpg, httpx, and more).

---

## Why asynchaos?

Microservices fail in the real world. Your tests should too.

```python
# Before: tests always succeed because dependencies are mocked
async def test_payment_service():
    result = await payment_client.charge(user_id=1, amount=100)
    assert result.status == "ok"

# After: test your retry logic, timeouts, and circuit breakers
async def test_payment_service_under_chaos():
    async with chaos_zone(latency_min_ms=200, drop_rate=0.3):
        with pytest.raises((ConnectionError, asyncio.TimeoutError)):
            result = await payment_client.charge(user_id=1, amount=100)
```

---

## Installation

```bash
pip install asynchaos
```

For development:

```bash
git clone https://github.com/async-chaos/asynchaos-python
cd asynchaos
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

---

## Quick Start

```python
import asyncio
import asynchaos
from asynchaos import inject_latency, drop_connections, timeout, chaos, chaos_zone

# Inject random 100–500ms latency on every call
@inject_latency(min_ms=100, max_ms=500)
async def fetch_user(user_id: int):
    ...

# Drop 10% of connections
@drop_connections(probability=0.1)
async def call_payment_api(order_id: str):
    ...

# Hard 2-second timeout
@timeout(seconds=2.0)
async def query_database(sql: str):
    ...

# All three combined — timeout fires even during the latency sleep
@chaos(latency_min_ms=200, latency_max_ms=800, drop_rate=0.05, timeout_seconds=1.0)
async def call_inventory_service(item_id: str):
    ...

# Scoped chaos zone — affects all decorated functions inside the block
async def test_resilience():
    async with chaos_zone(latency_min_ms=500, latency_max_ms=1000, drop_rate=0.2):
        await fetch_user(123)           # uses zone's 500–1000ms latency
        await call_payment_api("ord1")  # uses zone's 20% drop rate
```

---

## API Reference

### Decorators

#### `@inject_latency(min_ms=100, max_ms=500, probability=1.0)`

Adds an `asyncio.sleep()` before each function call. The sleep yields control to the event loop so other tasks run during the delay.

```python
from asynchaos import inject_latency
from asynchaos.conditions import RateCondition

# Fixed 200ms delay on every call
@inject_latency(min_ms=200, max_ms=200)
async def fn(): ...

# Random 100–500ms delay, 50% of the time
@inject_latency(min_ms=100, max_ms=500, probability=0.5)
async def fn(): ...

# Fail on first 2 of every 5 calls (circuit-breaker pattern)
@inject_latency(min_ms=300, probability=RateCondition(fail_count=2, window=5))
async def fn(): ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_ms` | `float` | `100.0` | Minimum delay in milliseconds |
| `max_ms` | `float` | `500.0` | Maximum delay in milliseconds |
| `probability` | `float` or `Condition` | `1.0` | Probability of injecting delay |

---

#### `@drop_connections(probability=0.1, exception=ConnectionError)`

Raises an exception before the function body executes, modeling a failed connection. The function is **never called** when the drop fires.

```python
from asynchaos import drop_connections
from asynchaos.exceptions import ConnectionDropped

# Drop 10% of calls (default ConnectionError)
@drop_connections(probability=0.1)
async def fn(): ...

# Use ConnectionDropped to distinguish injected vs real errors in tests
@drop_connections(probability=0.1, exception=ConnectionDropped)
async def fn(): ...

# Custom exception for domain-specific error handling
class ServiceUnavailable(Exception): pass

@drop_connections(probability=0.05, exception=ServiceUnavailable)
async def fn(): ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `probability` | `float` or `Condition` | `0.1` | Probability of dropping |
| `exception` | `Type[Exception]` | `ConnectionError` | Exception class to raise |

---

#### `@timeout(seconds, exception=asyncio.TimeoutError)`

Wraps the function with `asyncio.wait_for()`. Cancels the inner task when the deadline fires and guarantees the coroutine is no longer running when it returns.

```python
from asynchaos import timeout

@timeout(seconds=1.0)
async def fn(): ...

# Custom exception
class DBTimeout(Exception): pass

@timeout(seconds=0.5, exception=DBTimeout)
async def fn(): ...
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `seconds` | `float` | Timeout budget in seconds (must be > 0) |
| `exception` | `Type[Exception]` | Exception raised on timeout |

---

#### `@chaos(**kwargs)`

Combines latency, drop, and timeout in a single decorator. Effects are applied in a specific order:

```
asyncio.wait_for(timeout_seconds)    ← fires even during the latency sleep
  latency sleep                      ← counts against timeout budget
    drop check                       ← fires before the function body
      fn(*args, **kwargs)
```

This means `@chaos(latency=300, timeout_seconds=0.2)` will **always** time out — correctly modeling a slow network that exhausts the timeout budget before the server responds.

```python
from asynchaos import chaos

@chaos(
    latency_min_ms=100,
    latency_max_ms=400,
    drop_rate=0.05,
    timeout_seconds=1.0,
)
async def fn(): ...

# Shorthand: sets both min and max latency to the same value
@chaos(latency=200, drop_rate=0.1)
async def fn(): ...
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `latency_min_ms` | `float` | `None` | Minimum latency (ms) |
| `latency_max_ms` | `float` | `None` | Maximum latency (ms) |
| `latency` | `float` | `None` | Shorthand: sets both min and max |
| `drop_rate` | `float` or `Condition` | `None` | Connection drop probability |
| `drop_exception` | `Type[Exception]` | `ConnectionError` | Drop exception class |
| `timeout_seconds` | `float` | `None` | Hard timeout budget |
| `timeout_exception` | `Type[Exception]` | `asyncio.TimeoutError` | Timeout exception class |

---

### `chaos_zone` — Scoped Chaos

`chaos_zone` is an async context manager that activates chaos for the duration of the `async with` block. All `@inject_latency`, `@drop_connections`, and `@chaos` decorated functions called inside the zone use the zone's parameters instead of their own defaults.

```python
from asynchaos import chaos_zone

async def test_checkout_resilience():
    async with chaos_zone(
        latency_min_ms=300,
        latency_max_ms=800,
        drop_rate=0.2,
        drop_exception=ConnectionError,
    ) as ctx:
        # All decorated calls inside use zone config
        await inventory_service.check_stock(item_id)
        await payment_service.authorize(card_token)

        # Manual injection for non-decorated code
        await ctx.inject_latency()
        ctx.maybe_drop()
```

**Propagation:** `asyncio.create_task()` snapshots the current context at creation time. Tasks created **inside** a zone inherit the zone config; tasks created **before** entering the zone are unaffected.

```python
async with chaos_zone(latency=500):
    task = asyncio.create_task(fn())  # WILL see 500ms latency
    await task

task2 = asyncio.create_task(fn())     # will NOT see latency
async with chaos_zone(latency=500):
    await task2                       # zone entered after task2 was created
```

**Nesting:** Inner zones fully override outer zones. On exit, the outer zone is restored.

```python
async with chaos_zone(latency=50):           # outer: 50ms
    async with chaos_zone(latency=200):      # inner: 200ms — overrides outer
        await fn()                           # sees 200ms
    await fn()                               # back to 50ms
```

---

### `chaos_patch` — Monkey-Patching

Patches async methods on a class for the duration of the context. Useful when you can't modify the function you want to chaos-test (e.g., third-party clients).

```python
from asynchaos.patch import chaos_patch
import aiohttp

async def test_http_client_resilience():
    with chaos_patch(
        aiohttp.ClientSession,
        ["get", "post"],
        latency_min_ms=200,
        drop_rate=0.3,
    ):
        async with aiohttp.ClientSession() as session:
            response = await session.get("https://api.example.com/users")
    # Methods restored here
```

The patch applies to the **class** (not an instance), so all instances created before or after are affected while active. Originals are always restored, even if the block raises an exception.

---

### Global Control

```python
import asynchaos

asynchaos.disable()               # All decorators, zones, and patches become no-ops
asynchaos.enable()                # Re-enable (default state)
asynchaos.configure(
    global_probability=0.5        # Scale ALL probabilities by 0.5 (50% of configured rate)
)
```

`global_probability` composes multiplicatively: a decorator with `probability=0.8` and `global_probability=0.5` yields an effective probability of `0.4`.

Setting `global_probability=0.0` is equivalent to calling `disable()`.

---

### Conditions

For precise control beyond simple probability, use `Condition` objects:

```python
from asynchaos.conditions import ProbabilityCondition, RateCondition

# Same as passing a float
ProbabilityCondition(0.3)

# Fail on the first 2 of every 5 calls
# fires on calls: 1, 2, 6, 7, 11, 12, ...
RateCondition(fail_count=2, window=5)
```

Pass any `Condition` as the `probability` argument:

```python
@inject_latency(min_ms=200, probability=RateCondition(fail_count=1, window=3))
async def fn(): ...
```

---

### Exception Hierarchy

```
ChaosException(Exception)
├── ConnectionDropped(ChaosException, ConnectionError)
│       Use as exception= to distinguish injected from real errors in tests
├── ChaosTimeout(ChaosException, TimeoutError)
│       Catchable as TimeoutError; distinct type for test assertions
└── LatencyInjected(ChaosException)
        For cases where raising is preferable to blocking
```

The dual-inheritance design means existing `except ConnectionError:` handlers continue to work with injected failures, while test code can assert on the specific chaos type.

---

## Testing Patterns

### pytest fixture for clean state

```python
# conftest.py
import pytest, asynchaos

@pytest.fixture(autouse=True)
def reset_chaos():
    asynchaos.enable()
    asynchaos.configure(global_probability=1.0)
    yield
    asynchaos.enable()
    asynchaos.configure(global_probability=1.0)
```

### Asserting on injected vs real errors

```python
from asynchaos.exceptions import ChaosException, ConnectionDropped

@drop_connections(probability=1.0, exception=ConnectionDropped)
async def fn(): ...

async def test_fn():
    with pytest.raises(ConnectionDropped) as exc_info:
        await fn()
    assert isinstance(exc_info.value, ChaosException)  # was injected
    assert isinstance(exc_info.value, ConnectionError)  # compatible with real errors
```

### Temporarily disable chaos

```python
async def test_baseline():
    asynchaos.disable()
    try:
        result = await my_service()
        assert result == expected
    finally:
        asynchaos.enable()
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
