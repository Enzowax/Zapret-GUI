"""TLS measurements independent of Tk and process management."""
import asyncio
from dataclasses import dataclass
import ssl
import statistics
import time


@dataclass(frozen=True)
class ProbeResult:
    attempts: int
    successes: int
    latency_ms: float | None
    error: str | None
    checked_at: float

    @property
    def reliable(self):
        return self.successes == self.attempts and self.attempts > 0 and self.error != "cancelled"


async def probe(host, timeout, samples, context, cancelled=lambda: False):
    latencies, error, attempted = [], None, 0
    for _ in range(samples):
        if cancelled():
            error = "cancelled"
            break
        attempted += 1
        writer = None
        start = time.perf_counter()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, 443, ssl=context, server_hostname=host), timeout)
            latencies.append((time.perf_counter() - start) * 1000)
        except (OSError, TimeoutError, ssl.SSLError) as exc:
            error = type(exc).__name__
        finally:
            if writer is not None:
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), min(timeout, 0.5))
                except (OSError, TimeoutError, AttributeError):
                    pass
    return ProbeResult(attempted, len(latencies), statistics.median(latencies) if latencies else None,
                       error, time.time())


def measure_hosts(hosts, timeout=3, samples=1, cancelled=lambda: False, context_factory=None):
    hosts = list(dict.fromkeys(hosts))
    if not hosts:
        return {}
    if samples < 1:
        raise ValueError("samples must be positive")
    context = (context_factory or ssl.create_default_context)()

    async def batch():
        results = await asyncio.gather(*(probe(h, timeout, samples, context, cancelled) for h in hosts))
        return dict(zip(hosts, results))

    return asyncio.run(batch())
