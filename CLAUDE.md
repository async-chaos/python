# asynchaos — CLAUDE.md

Codebase guide for AI-assisted development.

## What this project is

`asynchaos` is a zero-dependency Python library for async chaos engineering. Developers decorate async functions with `@inject_latency`, `@drop_connections`, `@timeout`, or `@chaos` to simulate network failures during integration tests. A `chaos_zone` context manager provides scoped overrides via Python's `contextvars`.

## Project structure

```
src/asynchaos/
  __init__.py      Public API surface
  exceptions.py   Exception hierarchy (no imports from this package)
  conditions.py   ProbabilityCondition, RateCondition, coerce_condition
  context.py      _GlobalConfig, _ZoneConfig, _CHAOS_ZONE_VAR, chaos_zone
  decorators.py   @inject_latency, @drop_connections, @timeout, @chaos
  patch.py        chaos_patch context manager
tests/
  conftest.py     autouse fixture: reset asynchaos state between tests
  test_conditions.py
  test_decorators.py
  test_context.py
  test_integration.py
```

## Running tests

```bash
pytest tests/ -v
pytest tests/test_decorators.py -v          # single file
pytest -k test_zone_propagates -v           # single test
pytest --cov=asynchaos --cov-report=term-missing tests/
```

`pytest-asyncio` is configured with `asyncio_mode = "auto"`. Never add `@pytest.mark.asyncio` to test functions — it is not needed and will generate warnings.

## Hard constraints

- **Zero runtime dependencies.** `dependencies = []` must stay empty. Dev deps live in `[project.optional-dependencies].dev` only.
- **Python 3.9+.** No walrus operator in hot paths, no `match` statements, no `TypeAlias` (3.10+), no `tomllib` (3.11+).
- **`from __future__ import annotations`** must be at the top of every `.py` file in `src/asynchaos/`.
- **No monkeypatching asyncio internals.** `patch.py` is for user-provided classes only.
- **Never add `@pytest.mark.asyncio`.** Auto mode is enabled globally.

## Architecture invariants

- **Global config reads must use `threading.RLock`** (not `asyncio.Lock`) — test setUp/tearDown is synchronous.
- **Zone config is read at CALL TIME, not decoration time.** The `_CHAOS_ZONE_VAR.get()` call is inside `wrapper()`, not inside `decorator()`.
- **`ContextVar.reset(token)` must run in `__aexit__` unconditionally** — do not guard it with an `if` that could be skipped on exceptions.
- **Probability composition is multiplicative:** `effective = global_probability × decorator_probability`. Never use additive or min/max.
- **All stateful objects (`RateCondition`, `_GlobalConfig`) must be thread-safe via `threading.Lock`.**

## Key ContextVar behavior (do not break)

`asyncio.create_task(coro)` snapshots the current context at the moment of the call, not at the moment the task runs. This means:

- Task created **inside** `chaos_zone` → inherits zone config
- Task created **outside** `chaos_zone` then awaited inside → does NOT get zone config

Tests in `test_context.py` and `test_integration.py` verify this behavior. Do not change the ContextVar setup/teardown in `chaos_zone.__aenter__` / `__aexit__`.

## Test patterns

- Use `time.perf_counter()` for elapsed-time assertions with tolerances: lower bound `≥ 0.8×expected`, upper bound `≤ 2×expected`
- Statistical tests (e.g., `test_drop_connections_statistical_rate`) use 1000 iterations with ±30% bounds to avoid flakiness
- `conftest.py` `autouse` fixture resets global state — do not add per-test setup/teardown for global config

## Building the package

```bash
python -m build
python -m zipfile -l dist/asynchaos-0.1.0-py3-none-any.whl  # verify py.typed is included
```
