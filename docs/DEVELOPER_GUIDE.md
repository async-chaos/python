# asynchaos Developer Guide

This guide is for contributors and for developers who want to understand the internals before building on top of asynchaos.

---

## Repository Layout

```
src/asynchaos/        Library source (src layout, zero runtime deps)
tests/                pytest-asyncio test suite
docs/                 Developer documentation
pyproject.toml        Package metadata and tool config
CLAUDE.md             AI assistant codebase guide
```

---

## Architecture Overview

asynchaos is built on three independent but composable pillars:

### 1. Global Configuration (`_GlobalConfig`)

A thread-safe process-global singleton in `context.py`. Stores two fields:

- `_enabled: bool` — library-wide kill switch
- `_global_probability: float` — multiplicative scalar for all probabilities

Uses `threading.RLock` (not `asyncio.Lock`) because:
- `asyncio.Lock` requires a running event loop
- Test setUp/tearDown is often synchronous
- Third-party event loops (uvloop) may run tasks on multiple OS threads
- `RLock` (reentrant) prevents the same thread from deadlocking if it calls `enable()` from inside `configure()`

### 2. Scoped State (`ContextVar`)

`_CHAOS_ZONE_VAR` in `context.py` is a `contextvars.ContextVar[Optional[_ZoneConfig]]`. It carries per-async-call-stack configuration.

**Why ContextVar, not a global?**

A global dict keyed by `asyncio.current_task()` would work for simple cases but fails when a task creates sub-tasks or when using `asyncio.gather`. `contextvars.ContextVar` propagates automatically through the asyncio task tree via Python's context snapshot mechanism.

**Task creation semantics:**

`asyncio.create_task(coro)` internally calls `contextvars.copy_context()` at the moment of creation. This snapshot-at-creation behavior means:

```python
# Task created INSIDE zone → inherits zone config in its snapshot
async with chaos_zone(latency=500):
    task = asyncio.create_task(fn())  # snapshot includes latency=500
    await task                        # runs with latency=500

# Task created OUTSIDE zone → snapshot does not include zone
task = asyncio.create_task(fn())      # snapshot: no zone
async with chaos_zone(latency=500):
    await task                        # still runs without latency
```

`asyncio.gather(*coros)` creates tasks at the moment it is called, so all coroutines passed to `gather` inside a zone get the zone's snapshot.

**Token-based reset:**

`ContextVar.set(value)` returns a `Token` that records the previous value. `ContextVar.reset(token)` restores exactly that previous value — regardless of any intermediate mutations. This is how nested zones work correctly and how `__aexit__` guarantees cleanup even when exceptions are raised:

```python
async def __aenter__(self):
    self._token = _CHAOS_ZONE_VAR.set(self._config)  # stores previous value in token

async def __aexit__(self, *_):
    _CHAOS_ZONE_VAR.reset(self._token)  # restores previous value (None or outer zone)
```

### 3. Decorators

All four decorators (`inject_latency`, `drop_connections`, `timeout`, `chaos`) follow the same pattern:

1. Validate `async def` at **decoration time** (not call time) — so `TypeError` appears at the `@decorator` line, not at first invocation
2. Build a `wrapper` that reads `_CHAOS_ZONE_VAR.get()` and `_global_config` at **call time** — so each invocation is dynamically aware of the current zone and global state
3. Use `functools.wraps(fn)` to preserve `__name__`, `__doc__`, and `__wrapped__`

**Probability composition:**

```
effective_probability = global_probability × decorator_probability
```

Multiplicative composition ensures `global_probability=0.0` acts as a full disable with no special-casing needed anywhere.

**Effect ordering in `@chaos`:**

```
asyncio.wait_for(timeout_seconds)   ← outermost
  latency sleep                     ← inside timeout budget
    drop check                      ← before fn body
      fn(*args, **kwargs)
```

The key implication: a timeout fires even if the function is still sleeping in the latency phase. This correctly models real-world slow networks where the timeout budget is exhausted before the server ever processes the request.

---

## Module Reference

### `exceptions.py`

Pure Python. No imports from the rest of the library. Defines the exception hierarchy with dual inheritance so library-specific and standard handlers both work.

### `conditions.py`

Pure Python (uses `random` and `threading`). No asyncio dependencies. `coerce_condition()` is the public entry point used throughout the library to normalize float/int/Condition inputs.

### `context.py`

Contains `_GlobalConfig`, `_ZoneConfig`, `_CHAOS_ZONE_VAR`, `chaos_zone`, and `_ChaosCtxProxy`. The only module that directly touches `contextvars` and `threading`.

### `decorators.py`

Contains all four decorators. Imports from `conditions.py` and `context.py`. No direct use of `contextvars` — reads the ContextVar via the already-exported `_CHAOS_ZONE_VAR`.

### `patch.py`

Contains `chaos_patch`. Uses `contextlib.contextmanager` (sync, not async) because class method patching itself is synchronous. Respects both `_CHAOS_ZONE_VAR` and `_global_config`.

### `__init__.py`

Public API surface. Re-exports everything users need. Contains the module-level `enable()`, `disable()`, `configure()` functions that delegate to `_global_config`.

---

## Running Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest tests/ -v

# Run a specific file
pytest tests/test_decorators.py -v

# Run with coverage
pytest --cov=asynchaos --cov-report=term-missing tests/

# Run a single test by name
pytest -k test_zone_propagates_into_subtask -v
```

**Note:** `pytest-asyncio` is configured with `asyncio_mode = "auto"` in `pyproject.toml`. You do **not** need `@pytest.mark.asyncio` on any test function — all `async def test_*` functions are automatically handled.

**Test isolation:** `tests/conftest.py` provides an `autouse` fixture that calls `asynchaos.enable()` and `asynchaos.configure(global_probability=1.0)` before and after every test, preventing global state leakage.

---

## Building and Publishing

```bash
# Build the wheel and sdist
pip install build
python -m build

# Inspect the wheel contents
python -m zipfile -l dist/asynchaos-0.1.0-py3-none-any.whl

# Test the build in a clean environment
pip install dist/asynchaos-0.1.0-py3-none-any.whl

# Publish to PyPI (requires twine and PyPI credentials)
pip install twine
twine upload dist/*

# Publish to TestPyPI first (recommended)
twine upload --repository testpypi dist/*
```

---

## Design Decisions

### Why `threading.RLock` not `asyncio.Lock`?

`asyncio.Lock` requires a running event loop to acquire. The global config (`asynchaos.disable()`, `asynchaos.configure()`) is meant to be callable from synchronous test setup code (e.g., `unittest.setUp`). `threading.RLock` works from any context and has negligible overhead given the contention window is just two boolean reads per decorator call.

### Why multiplicative probability composition?

When `global_probability=0.5` and a decorator has `probability=0.8`, the effective rate is `0.4`. Alternatives:
- **Minimum:** `min(0.5, 0.8) = 0.5` — less intuitive, doesn't satisfy `global_probability=0.0 ⟹ always disabled`
- **Maximum:** counterintuitive
- **Additive clamp:** not monotone

Multiplicative is the only design where `global_probability=0.0` disables everything without requiring a special check in every decorator.

### Why are zones not influenced by each other's drop conditions?

A `chaos_zone` with `drop_rate=0.2` overrides the decorator's `probability` for that call. But two nested zones: the inner zone's config completely replaces the outer zone's config for the duration of the inner zone. This is intentional — zones are meant to be independent scopes, not accumulating stacks.

### Why is `py.typed` included?

`py.typed` (PEP 561) signals to mypy, pyright, and IDEs that this package ships inline type annotations. Without it, type checkers silently ignore the package's annotations. It costs nothing and makes asynchaos first-class for typed codebases.

---

## Contribution Guidelines

1. **No runtime dependencies** — `dependencies = []` in `pyproject.toml`. Never add a runtime dep.
2. **Python 3.9+ compatible** — no walrus operator (`:=`) in hot paths, no `match` statements, no `TypeAlias`
3. **`from __future__ import annotations`** at the top of every source file for forward reference support
4. **Thread-safety** — any stateful object (`RateCondition`, `_GlobalConfig`) must use `threading.Lock`
5. **No monkeypatching stdlib** — `patch.py` is for user-provided classes only, never for `asyncio` internals
6. **Read config at call time** — never cache zone config or global config outside a decorator call
7. **Test timing assertions with tolerances** — use `>=` lower bounds and generous upper bounds (`2x` expected) to avoid flakiness on CI
