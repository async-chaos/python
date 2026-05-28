"""
Getting Started with asynchaos
-------------------------------
Covers the four core decorators and global control in under 60 lines.
Run with:  python examples/getting_started.py
"""

from __future__ import annotations

import asyncio
import time

import asynchaos
from asynchaos import chaos, drop_connections, inject_latency, timeout


@inject_latency(min_ms=50, max_ms=150)
async def fetch_config(key: str) -> str:
    return f"value-of-{key}"


@drop_connections(probability=0.4)
async def send_event(event: str) -> None:
    pass  # fire-and-forget to an analytics service


@timeout(seconds=0.2)
async def slow_vendor_api(payload: dict) -> dict:
    await asyncio.sleep(0.5)   # vendor is reliably over budget
    return {}


@chaos(latency_min_ms=20, latency_max_ms=80, drop_rate=0.1, timeout_seconds=1.0)
async def payment_charge(amount: float) -> str:
    return f"charged:{amount}"


async def main() -> None:
    # @inject_latency adds a random sleep — other tasks continue during the wait
    start = time.perf_counter()
    value = await fetch_config("feature-flag")
    print(f"fetch_config: '{value}'  ({(time.perf_counter()-start)*1000:.0f}ms)")

    # @drop_connections raises ConnectionError ~40% of the time
    drops = 0
    for _ in range(20):
        if not await _try(send_event("page-view")):
            drops += 1
    print(f"send_event: {drops}/20 dropped  (expected ~8, ±4)")

    # @timeout cancels the coroutine when the deadline fires
    try:
        await slow_vendor_api({"ref": "abc"})
    except asyncio.TimeoutError:
        print("slow_vendor_api: TimeoutError raised  (deadline 200ms, call takes 500ms)")

    # @chaos combines all three — timeout fires even during the latency sleep
    successes = 0
    for _ in range(10):
        try:
            await payment_charge(9.99)
            successes += 1
        except (ConnectionError, asyncio.TimeoutError):
            pass
    print(f"payment_charge: {successes}/10 succeeded under combined chaos")

    # asynchaos.disable() makes every decorator a no-op instantly
    asynchaos.disable()
    start = time.perf_counter()
    await fetch_config("x")
    print(f"fetch_config with chaos disabled: {(time.perf_counter()-start)*1000:.1f}ms  (expected <1ms)")
    asynchaos.enable()

    print("\nDone.")


async def _try(coro) -> bool:
    try:
        await coro
        return True
    except Exception:
        return False


if __name__ == "__main__":
    asyncio.run(main())
