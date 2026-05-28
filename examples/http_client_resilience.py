"""
HTTP Client Resilience
----------------------
Simulates an aiohttp-style async HTTP client and shows how chaos_patch
lets you inject faults into third-party clients without touching their code.

Real-world scenario: your service calls a downstream REST API.
You want to verify that your retry logic, circuit breaker, and
timeout fallback actually work when the API misbehaves.

Run with:  python examples/http_client_resilience.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from asynchaos import chaos_zone
from asynchaos.patch import chaos_patch


# ---------------------------------------------------------------------------
# Stand-in for aiohttp.ClientSession or httpx.AsyncClient
# (swap in the real class name and your chaos_patch call needs no other changes)
# ---------------------------------------------------------------------------

@dataclass
class Response:
    status: int
    body: dict


class AsyncHttpClient:
    """Minimal stand-in for aiohttp.ClientSession."""

    async def get(self, url: str, **kwargs) -> Response:
        return Response(status=200, body={"url": url, "data": []})

    async def post(self, url: str, json: dict, **kwargs) -> Response:
        return Response(status=201, body={"created": True})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass


# ---------------------------------------------------------------------------
# Application layer — calls the HTTP client, handles errors
# ---------------------------------------------------------------------------

MAX_RETRIES = 3
RETRY_BACKOFF_MS = 100


async def fetch_products(client: AsyncHttpClient, page: int = 1) -> list:
    """Fetches a paginated product list with exponential-backoff retry."""
    url = f"https://api.shop.example.com/products?page={page}"
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = await client.get(url)
            if resp.status == 200:
                return resp.body.get("data", [])
            raise ConnectionError(f"HTTP {resp.status}")
        except ConnectionError as exc:
            if attempt == MAX_RETRIES:
                raise
            delay = RETRY_BACKOFF_MS * (2 ** (attempt - 1)) / 1000
            print(f"    attempt {attempt} failed ({exc}), retrying in {delay*1000:.0f}ms")
            await asyncio.sleep(delay)
    return []


async def submit_order(client: AsyncHttpClient, order: dict) -> bool:
    """Submits an order; returns False instead of raising on connection errors."""
    try:
        resp = await client.post("https://api.shop.example.com/orders", json=order)
        return resp.status == 201
    except ConnectionError:
        return False


# ---------------------------------------------------------------------------
# Chaos experiments
# ---------------------------------------------------------------------------

async def experiment_latency_degradation() -> None:
    """How does p50/p95 latency look when the API is slow?"""
    print("=== Experiment: API latency degradation ===")
    client = AsyncHttpClient()
    timings = []

    with chaos_patch(AsyncHttpClient, ["get"], latency_min_ms=80, latency_max_ms=300):
        for _ in range(10):
            start = time.perf_counter()
            await fetch_products(client)
            timings.append((time.perf_counter() - start) * 1000)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[int(len(timings) * 0.95)]
    print(f"  p50: {p50:.0f}ms  p95: {p95:.0f}ms  (injected 80–300ms per call)")


async def experiment_retry_under_partial_outage() -> None:
    """Verify retries recover from a 50% drop rate."""
    print("\n=== Experiment: 50% drop rate — does retry recover? ===")
    client = AsyncHttpClient()
    results = {"success": 0, "exhausted": 0}

    with chaos_patch(AsyncHttpClient, ["get"], drop_rate=0.5):
        for i in range(5):
            try:
                await fetch_products(client, page=i)
                results["success"] += 1
            except ConnectionError:
                results["exhausted"] += 1

    print(f"  results: {results}  (retries={MAX_RETRIES}, expected most to succeed)")


async def experiment_full_outage_order_submission() -> None:
    """Confirm order submission returns False (not raises) during a full outage."""
    print("\n=== Experiment: full outage — order submission degraded gracefully? ===")
    client = AsyncHttpClient()

    with chaos_patch(AsyncHttpClient, ["post"], drop_rate=1.0):
        result = await submit_order(client, {"item": "widget", "qty": 2})

    print(f"  submit_order returned: {result}  (expected False, not an unhandled exception)")
    assert result is False, "submit_order should return False on connection error"
    print("  PASS: degraded gracefully")


async def experiment_chaos_zone_scoped_to_test() -> None:
    """Use chaos_zone to scope chaos to one test path without patching the class."""
    print("\n=== Experiment: chaos_zone scoped latency ===")
    client = AsyncHttpClient()

    # Decorate the method we care about
    from asynchaos import inject_latency

    @inject_latency(min_ms=10, max_ms=10)
    async def _patched_get(url, **kw):
        return await client.get(url, **kw)

    # Baseline
    start = time.perf_counter()
    await client.get("https://api.shop.example.com/products")
    baseline = (time.perf_counter() - start) * 1000

    # Under zone
    async with chaos_zone(latency=200):
        start = time.perf_counter()
        await _patched_get("https://api.shop.example.com/products")
        under_zone = (time.perf_counter() - start) * 1000

    print(f"  baseline: {baseline:.1f}ms  |  under chaos_zone(latency=200): {under_zone:.0f}ms")


async def main() -> None:
    await experiment_latency_degradation()
    await experiment_retry_under_partial_outage()
    await experiment_full_outage_order_submission()
    await experiment_chaos_zone_scoped_to_test()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
