"""
Microservice Pipeline Resilience
---------------------------------
Simulates a chain of three services (auth → inventory → pricing) that must
complete within an end-to-end SLA, with fallback behaviour at each hop.

Real-world scenario: a checkout API that calls auth, inventory, and pricing
services in sequence. Any hop can be slow or unavailable. You need to know:
  - Does a single slow service blow the end-to-end SLA?
  - Does the fallback pricing activate when the pricing service is down?
  - Does an auth failure propagate correctly (no silent 200)?

Run with:  python examples/microservice_pipeline.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from asynchaos import chaos_zone, inject_latency, timeout
from asynchaos.patch import chaos_patch


# ---------------------------------------------------------------------------
# Downstream service clients
# ---------------------------------------------------------------------------

class AuthService:
    async def verify_token(self, token: str) -> dict:
        return {"user_id": "u-001", "scopes": ["read", "write"]}


class InventoryService:
    async def check_stock(self, item_id: str) -> dict:
        return {"item_id": item_id, "in_stock": True, "quantity": 42}


class PricingService:
    async def get_price(self, item_id: str, user_id: str) -> float:
        return 29.99


# ---------------------------------------------------------------------------
# Checkout pipeline — calls all three services
# ---------------------------------------------------------------------------

END_TO_END_SLA_MS = 500
FALLBACK_PRICE = 0.0


@dataclass
class CheckoutResult:
    user_id: str
    item_id: str
    in_stock: bool
    price: float
    degraded: bool = False   # True if fallback pricing was used


@timeout(seconds=END_TO_END_SLA_MS / 1000)
async def checkout(
    token: str,
    item_id: str,
    auth: AuthService,
    inventory: InventoryService,
    pricing: PricingService,
) -> CheckoutResult:
    """
    Calls auth, inventory, and pricing in sequence.
    Auth and inventory failures propagate; pricing failure uses a fallback.
    The whole pipeline is bounded by END_TO_END_SLA_MS.
    """
    # Auth: must succeed — no fallback
    identity = await auth.verify_token(token)

    # Inventory: must succeed — no fallback
    stock = await inventory.check_stock(item_id)

    # Pricing: best-effort with fallback
    degraded = False
    try:
        price = await asyncio.wait_for(
            pricing.get_price(item_id, identity["user_id"]),
            timeout=0.15,   # inner 150ms budget for pricing
        )
    except (asyncio.TimeoutError, ConnectionError):
        price = FALLBACK_PRICE
        degraded = True

    return CheckoutResult(
        user_id=identity["user_id"],
        item_id=item_id,
        in_stock=stock["in_stock"],
        price=price,
        degraded=degraded,
    )


def _make_services():
    return AuthService(), InventoryService(), PricingService()


# ---------------------------------------------------------------------------
# Chaos experiments
# ---------------------------------------------------------------------------

async def experiment_baseline() -> None:
    print("=== Baseline (no chaos) ===")
    auth, inv, pricing = _make_services()
    start = time.perf_counter()
    result = await checkout("tok-abc", "item-99", auth, inv, pricing)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"  result: {result}")
    print(f"  elapsed: {elapsed:.1f}ms  (expected <5ms)")


async def experiment_single_slow_hop_blows_sla() -> None:
    """A 600ms inventory call exceeds the 500ms end-to-end SLA."""
    print(f"\n=== Experiment: inventory latency 600ms > {END_TO_END_SLA_MS}ms SLA ===")
    auth, inv, pricing = _make_services()

    with chaos_patch(InventoryService, ["check_stock"], latency=600):
        try:
            await checkout("tok-abc", "item-99", auth, inv, pricing)
            print("  FAIL: expected TimeoutError")
        except asyncio.TimeoutError:
            print(f"  PASS: end-to-end TimeoutError raised (inventory was 600ms, SLA is {END_TO_END_SLA_MS}ms)")


async def experiment_pricing_fallback_on_timeout() -> None:
    """When pricing is slow, checkout still completes with fallback price."""
    print("\n=== Experiment: pricing service slow — fallback price used ===")
    auth, inv, pricing = _make_services()

    with chaos_patch(PricingService, ["get_price"], latency=300):
        result = await checkout("tok-abc", "item-99", auth, inv, pricing)

    print(f"  result: {result}")
    assert result.degraded, "Expected degraded=True when pricing timed out"
    assert result.price == FALLBACK_PRICE
    print(f"  PASS: checkout succeeded with fallback price={result.price} (degraded={result.degraded})")


async def experiment_auth_failure_propagates() -> None:
    """An auth failure must propagate — no silent checkout with unknown user."""
    print("\n=== Experiment: auth service down — connection error propagates ===")
    auth, inv, pricing = _make_services()

    with chaos_patch(AuthService, ["verify_token"], drop_rate=1.0):
        try:
            await checkout("tok-abc", "item-99", auth, inv, pricing)
            print("  FAIL: expected ConnectionError")
        except ConnectionError as exc:
            print(f"  PASS: ConnectionError propagated from auth — '{exc}'")


async def experiment_all_services_under_realistic_load() -> None:
    """
    All three services have light latency (10–50ms each).
    End-to-end should stay well within the 500ms SLA.
    """
    print("\n=== Experiment: all services with realistic 10–50ms latency ===")
    auth, inv, pricing = _make_services()
    timings = []

    with chaos_patch(AuthService, ["verify_token"], latency_min_ms=10, latency_max_ms=30):
        with chaos_patch(InventoryService, ["check_stock"], latency_min_ms=20, latency_max_ms=50):
            with chaos_patch(PricingService, ["get_price"], latency_min_ms=10, latency_max_ms=40):
                for _ in range(10):
                    start = time.perf_counter()
                    result = await checkout("tok-abc", "item-99", auth, inv, pricing)
                    timings.append((time.perf_counter() - start) * 1000)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[int(len(timings) * 0.95)]
    over_sla = sum(1 for t in timings if t > END_TO_END_SLA_MS)
    print(f"  p50: {p50:.0f}ms  p95: {p95:.0f}ms  over-SLA: {over_sla}/10")
    print(f"  {'PASS' if over_sla == 0 else 'WARN'}: all requests within {END_TO_END_SLA_MS}ms SLA")


async def experiment_scoped_chaos_single_hop() -> None:
    """
    Patch only one hop (inventory) to be slow for a subset of requests,
    leaving auth and pricing unaffected — mirrors canary / dark-launch testing.
    """
    print("\n=== Experiment: chaos scoped to inventory hop only ===")
    auth, inv, pricing = _make_services()

    # Normal request — no chaos on any hop
    start = time.perf_counter()
    normal = await checkout("tok-normal", "item-1", auth, inv, pricing)
    normal_ms = (time.perf_counter() - start) * 1000

    # Chaos request — only inventory is slow (200ms), auth and pricing are unaffected
    with chaos_patch(InventoryService, ["check_stock"], latency=200):
        start = time.perf_counter()
        chaotic = await checkout("tok-test", "item-1", auth, inv, pricing)
        chaos_ms = (time.perf_counter() - start) * 1000

    print(f"  normal request:  {normal_ms:.0f}ms  degraded={normal.degraded}")
    print(f"  chaotic request: {chaos_ms:.0f}ms  degraded={chaotic.degraded}")
    print(f"  (only inventory patched with 200ms — auth and pricing untouched)")


async def main() -> None:
    await experiment_baseline()
    await experiment_single_slow_hop_blows_sla()
    await experiment_pricing_fallback_on_timeout()
    await experiment_auth_failure_propagates()
    await experiment_all_services_under_realistic_load()
    await experiment_scoped_chaos_single_hop()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
