# asynchaos — CLAUDE.md

This file is loaded automatically by Claude Code on every session in this directory.
It captures the full project context so nothing needs to be re-explained.

---

## What this project is

`asynchaos` is a zero-dependency Python library for async chaos engineering, published on PyPI.
Developers decorate async functions with `@inject_latency`, `@drop_connections`, `@timeout`,
or `@chaos` to simulate network failures, database timeouts, and race conditions during
integration tests. A `chaos_zone` context manager provides scoped overrides via Python's
`contextvars`. Target audience: FastAPI / microservices developers.

**GitHub:** https://github.com/async-chaos/asynchaos-python
**Package name:** `asynchaos` (PyPI, not yet published — release workflow is ready)
**Version:** 0.1.0
**License:** Apache 2.0

---

## Repository layout

```
src/asynchaos/          Library source (src layout, zero runtime deps)
  __init__.py           Public API: all exports + enable/disable/configure
  exceptions.py         ChaosException, ConnectionDropped, ChaosTimeout, LatencyInjected
  conditions.py         Condition, ProbabilityCondition, RateCondition, coerce_condition
  context.py            _GlobalConfig, _ZoneConfig, _CHAOS_ZONE_VAR, chaos_zone, _ChaosCtxProxy
  decorators.py         @inject_latency, @drop_connections, @timeout, @chaos
  patch.py              chaos_patch context manager (monkey-patches async client classes)
  py.typed              PEP 561 typed-package marker

tests/
  conftest.py           autouse fixture: reset asynchaos global state between every test
  test_conditions.py    ProbabilityCondition, RateCondition, coerce_condition (20 tests)
  test_decorators.py    All four decorators, parametrized with descriptive chaos scenario IDs
  test_context.py       chaos_zone: override, nesting, ContextVar, task propagation, proxy
  test_integration.py   chaos_patch + concurrent tasks + global disable (12 tests)

examples/
  getting_started.py        All four decorators and global control (~60 lines)
  http_client_resilience.py chaos_patch on aiohttp-style client; retry under partial outage
  database_resilience.py    Query timeout SLA, reconnect on DB blip, concurrent queries
  payment_gateway.py        Idempotency under timeouts, deterministic failure windows
  microservice_pipeline.py  End-to-end SLA across auth → inventory → pricing chain

.github/workflows/
  ci.yml                Test matrix (Python 3.9–3.12) + Examples job + Lint/dep-guard
  release.yml           Triggered on v* tags: builds wheel+sdist, creates GitHub release
                        PyPI publish job is scaffolded but commented out

docs/
  DEVELOPER_GUIDE.md    Architecture internals, ContextVar deep-dive, design decisions
```

---

## Development environment

```bash
# Activate venv (already created at .venv/)
source .venv/bin/activate

# Install / reinstall
pip install -e ".[dev]"

# Run tests
pytest tests/ -v
pytest tests/test_decorators.py -v
pytest -k "zone_task" -v
pytest --cov=asynchaos --cov-report=term-missing tests/

# Run examples
python examples/getting_started.py
for f in examples/*.py; do python "$f"; done

# Build wheel
python -m build
python -m zipfile -l dist/asynchaos-0.1.0-py3-none-any.whl
```

---

## Public API (complete)

```python
# Decorators
from asynchaos import inject_latency, drop_connections, timeout, chaos

# Scoped context manager
from asynchaos import chaos_zone

# Monkey-patching helper
from asynchaos.patch import chaos_patch

# Global control
import asynchaos
asynchaos.enable()
asynchaos.disable()
asynchaos.configure(global_probability=0.5)

# Conditions
from asynchaos.conditions import ProbabilityCondition, RateCondition

# Exceptions
from asynchaos.exceptions import ChaosException, ConnectionDropped, ChaosTimeout, LatencyInjected
```

---

## Architecture: three pillars

### 1. `_GlobalConfig` (threading.RLock singleton)
- Lives in `context.py`, singleton `_global_config`
- Stores `_enabled: bool` and `_global_probability: float`
- Uses `threading.RLock` (NOT `asyncio.Lock`) — test setUp/tearDown is synchronous
- `asynchaos.enable()` / `disable()` / `configure()` delegate to this object

### 2. `_CHAOS_ZONE_VAR` (ContextVar)
- Lives in `context.py`, module-level `ContextVar[Optional[_ZoneConfig]]`
- `chaos_zone.__aenter__` calls `_CHAOS_ZONE_VAR.set(config)` and stores the token
- `chaos_zone.__aexit__` calls `_CHAOS_ZONE_VAR.reset(token)` — ALWAYS, even on exception
- `asyncio.create_task()` snapshots context at creation time — tasks created INSIDE a zone
  inherit the zone config; tasks created BEFORE entering the zone do NOT
- Nested zones work correctly via token reset — inner restores outer on exit

### 3. Decorators (read config at call time)
- All four decorators read `_CHAOS_ZONE_VAR.get()` and `_global_config` inside `wrapper()`,
  NOT inside `decorator()`. This is the key invariant — never cache zone config.
- `functools.wraps(fn)` on every wrapper
- `_require_async(fn, name)` validates `async def` at DECORATION time, not call time

**Effect ordering in `@chaos`:**
```
asyncio.wait_for(timeout_seconds)    ← outermost: fires even during latency sleep
  latency sleep                      ← counts against timeout budget
    drop check                       ← fires before fn body
      fn(*args, **kwargs)
```
`chaos(latency=500, timeout_seconds=0.2)` will ALWAYS timeout — this is intentional.

**Probability composition:** `effective = global_probability × decorator_probability`
Multiplicative so `global_probability=0.0` disables everything without special-casing.

---

## Hard constraints (never violate)

- **`dependencies = []` in pyproject.toml** — zero runtime dependencies. Dev deps only in
  `[project.optional-dependencies].dev`.
- **Python 3.9+** — no walrus operator (`:=`) in hot paths, no `match`, no `TypeAlias`,
  no `tomllib`. `from __future__ import annotations` at top of every `src/asynchaos/*.py`.
- **Never add `@pytest.mark.asyncio`** — `asyncio_mode = "auto"` is set globally in
  `pyproject.toml`. Adding the mark generates warnings.
- **No monkeypatching asyncio internals** — `patch.py` is for user-provided classes only.
- **`ContextVar.reset(token)` must run unconditionally in `__aexit__`** — never guard it.
- **All stateful objects use `threading.Lock`** — `RateCondition`, `_GlobalConfig`.
- **`await` cannot appear in generator expressions** — use explicit loops instead.
  (`sum(1 for _ in range(n) if not await coro())` is invalid Python)

---

## Test conventions

- **Parametrize with descriptive IDs** — test IDs describe the chaos scenario, e.g.:
  `test_chaos_combined[chaos(latency=500ms, timeout=0.05s) → timeout fires during latency sleep]`
  This makes the pytest -v output self-documenting without any flags or plugins.
- **Timing assertions:** lower bound `≥ 0.8×expected_ms`, upper bound `≤ 2×expected_ms`
- **Statistical tests** (e.g., probability=0.5 over 1000 calls) use ±30% bounds
- **`conftest.py` autouse fixture** resets `asynchaos.enable()` + `global_probability=1.0`
  before and after every test — do NOT add per-test global state management
- **`_try(coro)` helper** in test_decorators.py swallows exceptions, returns bool

---

## CI / GitHub Actions

**ci.yml** triggers on push and PR to `main`:
- `test` job: matrix across Python 3.9, 3.10, 3.11, 3.12; runs pytest with coverage
- `examples` job: runs every `examples/*.py` on Python 3.11
- `lint` job: imports asynchaos, verifies zero runtime deps via `importlib.metadata`

**release.yml** triggers on `v*` tags:
- Runs full test suite, then `python -m build`
- Verifies `py.typed` is in the wheel
- Creates a GitHub Release with wheel + sdist attached
- PyPI publish job exists but is **commented out** — uncomment + configure OIDC
  trusted publishing when ready to publish

**To release:**
```bash
git tag v0.1.0
git push --tags
# GitHub Release is created automatically with wheel/sdist attached
# Then enable the publish-pypi job in release.yml for PyPI
```

---

## Key decisions made (don't re-debate without reason)

| Decision | Rationale |
|----------|-----------|
| `threading.RLock` for global config | `asyncio.Lock` needs a running event loop; test setup is sync |
| Multiplicative probability composition | `global_prob=0.0` cleanly disables all without special-cases |
| Timeout wraps latency (not outside it) | Models real networks: slow latency exhausts the timeout budget |
| `async def` validated at decoration time | TypeError appears at `@decorator` line, not at first call |
| `create_task()` context snapshot is a feature | Explicit: tasks inside zone get chaos, tasks outside don't |
| Dual-inheritance exceptions | `ConnectionDropped(ChaosException, ConnectionError)` — catchable by both |
| `py.typed` marker | PEP 561: typed package, works with mypy/pyright out of the box |

---

## What has been built (session history)

1. Package scaffold (`pyproject.toml`, `src/` layout, `py.typed`)
2. Exception hierarchy and Condition system (`ProbabilityCondition`, `RateCondition`)
3. `@inject_latency` — asyncio.sleep with global config + ContextVar stub
4. `@drop_connections` — probabilistic raise before function body
5. `@timeout` — `asyncio.wait_for` with cancellation safety
6. `@chaos` — combined decorator with ordered latency → drop → fn inside wait_for
7. `chaos_zone` — full ContextVar machinery with token reset, `_ChaosCtxProxy`
8. `chaos_patch` — monkey-patches async methods on a class, always restores
9. Full test suite — 83 tests, parametrized with descriptive chaos scenario IDs
10. README with full API reference, quickstart, test output example
11. `docs/DEVELOPER_GUIDE.md` — architecture internals deep-dive
12. GitHub repo: `async-chaos/asynchaos-python`
13. CI workflows: test matrix (3.9–3.12), examples runner, lint, release
14. 5 real-world examples: getting_started, http_client, database, payment_gateway, microservice_pipeline
15. Apache 2.0 LICENSE file
