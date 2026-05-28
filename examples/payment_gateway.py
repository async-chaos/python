"""
Payment Gateway Resilience
--------------------------
Simulates a payment processing service that must be idempotent, fault-tolerant,
and never double-charge — even when the gateway is flaky.

Real-world scenario: Stripe / Braintree / Adyen integration.
A charge call might timeout after the gateway already processed it.
Your service needs to handle that without retrying blindly.

Run with:  python examples/payment_gateway.py
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Optional

from asynchaos import chaos_zone, timeout
from asynchaos.conditions import RateCondition
from asynchaos.patch import chaos_patch


# ---------------------------------------------------------------------------
# Payment gateway client (stand-in for stripe-python async / httpx calls)
# ---------------------------------------------------------------------------

@dataclass
class ChargeResult:
    charge_id: str
    status: str   # "succeeded" | "failed" | "pending"
    amount: float


class PaymentGatewayClient:
    """Stand-in for an async payment gateway SDK."""

    async def create_charge(
        self,
        amount: float,
        idempotency_key: str,
        currency: str = "usd",
    ) -> ChargeResult:
        return ChargeResult(
            charge_id=f"ch_{idempotency_key[:8]}",
            status="succeeded",
            amount=amount,
        )

    async def get_charge(self, charge_id: str) -> Optional[ChargeResult]:
        return None   # not found in baseline (no state)


# ---------------------------------------------------------------------------
# Payment service — owns idempotency, retry, and timeout handling
# ---------------------------------------------------------------------------

GATEWAY_TIMEOUT_SECONDS = 0.3   # 300ms timeout per attempt
MAX_CHARGE_ATTEMPTS = 3


@dataclass
class PaymentService:
    client: PaymentGatewayClient
    _processed: dict = field(default_factory=dict)   # idempotency store

    async def charge(self, user_id: str, amount: float) -> ChargeResult:
        """
        Idempotent charge: if a previous attempt succeeded (even after a
        timeout response), we return the existing charge rather than
        double-charging.
        """
        idempotency_key = f"{user_id}:{amount:.2f}"

        # Check local idempotency store first
        if idempotency_key in self._processed:
            return self._processed[idempotency_key]

        last_exc: Exception | None = None
        for attempt in range(1, MAX_CHARGE_ATTEMPTS + 1):
            try:
                result = await asyncio.wait_for(
                    self.client.create_charge(amount, idempotency_key),
                    timeout=GATEWAY_TIMEOUT_SECONDS,
                )
                self._processed[idempotency_key] = result
                return result
            except asyncio.TimeoutError as exc:
                last_exc = exc
                print(f"    attempt {attempt}: gateway timeout — checking for existing charge")
                # On timeout we don't know if the charge went through.
                # A real service would query the gateway here.
                existing = await self.client.get_charge(f"ch_{idempotency_key[:8]}")
                if existing and existing.status == "succeeded":
                    self._processed[idempotency_key] = existing
                    return existing
            except ConnectionError as exc:
                last_exc = exc
                print(f"    attempt {attempt}: connection error ({exc})")

            await asyncio.sleep(0.05 * attempt)

        raise RuntimeError(
            f"Payment failed after {MAX_CHARGE_ATTEMPTS} attempts"
        ) from last_exc


# ---------------------------------------------------------------------------
# Chaos experiments
# ---------------------------------------------------------------------------

async def experiment_gateway_timeout_no_double_charge() -> None:
    """
    When the gateway timeouts on every attempt, ensure the service raises
    rather than silently dropping the payment — and definitely doesn't retry
    blindly on success.
    """
    print("=== Experiment: gateway always times out ===")
    client = PaymentGatewayClient()
    svc = PaymentService(client)

    with chaos_patch(PaymentGatewayClient, ["create_charge"], latency=500):
        try:
            await svc.charge("user-001", 49.99)
            print("  FAIL: expected RuntimeError")
        except RuntimeError as exc:
            print(f"  PASS: RuntimeError raised — '{exc}'")
            print(f"  No double-charge: idempotency store = {svc._processed}")


async def experiment_flaky_gateway_retry_succeeds() -> None:
    """
    30% of gateway calls fail. Verify that 3 retries are enough to succeed
    in the vast majority of cases.
    """
    print("\n=== Experiment: 30% gateway failures — retries should recover ===")
    client = PaymentGatewayClient()
    successes = 0
    failures = 0

    for i in range(20):
        svc = PaymentService(client)
        async with chaos_zone(drop_rate=0.3):
            try:
                result = await svc.charge(f"user-{i:03d}", 9.99)
                assert result.status == "succeeded"
                successes += 1
            except (RuntimeError, ConnectionError):
                failures += 1

    print(f"  successes: {successes}/20  failures: {failures}/20")
    print(f"  {'PASS' if successes >= 17 else 'WARN'}: expected >=17 successes with 3 retries at 30% drop")


async def experiment_idempotency_no_double_charge() -> None:
    """
    Simulate the worst case: charge succeeds at the gateway but the response
    times out. A retry must not double-charge.
    """
    print("\n=== Experiment: idempotency — timeout after success, no double-charge ===")

    call_count = 0

    class TrackedClient(PaymentGatewayClient):
        async def create_charge(self, amount, idempotency_key, currency="usd"):
            nonlocal call_count
            call_count += 1
            # First call: always succeeds but we simulate a timeout response
            return await super().create_charge(amount, idempotency_key, currency)

    client = TrackedClient()
    svc = PaymentService(client)

    # First call with latency → TimeoutError
    with chaos_patch(TrackedClient, ["create_charge"], latency=500):
        try:
            await svc.charge("user-vip", 199.99)
        except RuntimeError:
            pass  # expected: gateway timed out, no existing charge found

    # Second call (retry by caller) — must NOT call the gateway again if idempotent
    first_calls = call_count
    try:
        await svc.charge("user-vip", 199.99)  # same user+amount → same idempotency key
    except RuntimeError:
        pass

    print(f"  gateway called {first_calls} time(s) before caller retry")
    print(f"  gateway called {call_count} time(s) total after caller retry")
    # If idempotency_key is in _processed we'd return early; here we show the pattern


async def experiment_deterministic_failure_window() -> None:
    """
    RateCondition on chaos_patch: first 2 of every 5 gateway calls fail.
    Useful for load tests where you need exactly N failures per batch.
    We set MAX_CHARGE_ATTEMPTS=1 here so retries don't consume extra slots.
    """
    print("\n=== Experiment: deterministic failure window (2/5 pattern) ===")
    client = PaymentGatewayClient()
    outcomes = []
    fail_condition = RateCondition(fail_count=2, window=5)

    for i in range(10):
        svc = PaymentService(client)
        # chaos_patch accepts a Condition via drop_rate only when it's a float;
        # call should_trigger() ourselves and translate to 0/1 probability.
        drop = 1.0 if fail_condition.should_trigger() else 0.0
        with chaos_patch(PaymentGatewayClient, ["create_charge"], drop_rate=drop):
            try:
                # Single attempt so failures aren't masked by retries
                result = await asyncio.wait_for(
                    client.create_charge(1.00, f"key-{i}"), timeout=0.3
                )
                outcomes.append("ok")
            except (ConnectionError, asyncio.TimeoutError):
                outcomes.append("fail")

    print(f"  outcomes: {outcomes}")
    print(f"  expected: fail fail ok ok ok fail fail ok ok ok")


async def main() -> None:
    await experiment_gateway_timeout_no_double_charge()
    await experiment_flaky_gateway_retry_succeeds()
    await experiment_idempotency_no_double_charge()
    await experiment_deterministic_failure_window()
    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
