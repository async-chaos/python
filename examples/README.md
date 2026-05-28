# asynchaos Examples

Runnable examples demonstrating asynchaos in realistic scenarios.
Each file is self-contained and exits 0 on success.

```bash
# Run a single example
python examples/getting_started.py

# Run all examples
for f in examples/*.py; do python "$f"; done
```

| File | What it demonstrates |
|------|----------------------|
| [`getting_started.py`](getting_started.py) | All four decorators and global control in ~60 lines |
| [`http_client_resilience.py`](http_client_resilience.py) | `chaos_patch` on an aiohttp-style client; retry under partial outage |
| [`database_resilience.py`](database_resilience.py) | Query timeout SLA, reconnect on DB blip, concurrent queries under latency |
| [`payment_gateway.py`](payment_gateway.py) | Idempotency under timeouts, flaky gateway retries, deterministic failure windows |
| [`microservice_pipeline.py`](microservice_pipeline.py) | End-to-end SLA across auth → inventory → pricing chain, per-service fallbacks |
