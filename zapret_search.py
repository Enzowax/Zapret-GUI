"""Candidate selection and search results, without UI state."""
from dataclasses import dataclass
import statistics


@dataclass(frozen=True)
class SearchResult:
    name: str
    per_service: dict
    total: int
    successes: int
    attempts: int
    latency_ms: float | None

    @property
    def rank(self):
        return (self.total, self.successes / self.attempts if self.attempts else 0,
                -(self.latency_ms if self.latency_ms is not None else float("inf")))


def summarize(name, targets, probes):
    per = {s: 0 for s, _ in targets}
    for service, host in targets:
        per[service] += int(probes[host].reliable)
    values = [x.latency_ms for x in probes.values() if x.latency_ms is not None]
    return SearchResult(name, per, sum(per.values()), sum(x.successes for x in probes.values()),
                        sum(x.attempts for x in probes.values()), statistics.median(values) if values else None)


def run_search(presets, targets, quick_hosts, trial, cancelled, fast, publish):
    """Quick probes only order candidates; no candidate is discarded by them."""
    ordered = []
    for index, preset in enumerate(presets):
        if cancelled():
            return None
        probes = trial(preset, quick_hosts, 1)
        score = sum(x.reliable for x in probes.values())
        ordered.append((-score, index, preset))
    results = []
    for _, _, preset in sorted(ordered):
        if cancelled():
            return None
        probes = trial(preset, [h for _, h in targets], 3)
        if cancelled():
            return None
        result = summarize(preset["name"], targets, probes)
        results.append(result)
        publish(result)
        if fast and sum(r.total == len(targets) for r in results) >= 3:
            break
    return sorted(results, key=lambda r: r.rank, reverse=True)
