import math
import tracemalloc
from collections.abc import Callable
from time import monotonic_ns

from .schemas import BenchmarkMode, BenchmarkSummary, ProviderKind


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return sorted_values[lower]
    weight = rank - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


class BenchmarkRunner:
    """Reproducible monotonic-clock microbenchmark harness."""

    def __init__(self, clock_ns: Callable[[], int] = monotonic_ns) -> None:
        self._clock_ns = clock_ns

    def run(
        self,
        workload_name: str,
        fn: Callable[[], object],
        *,
        iterations: int = 20,
        warmup_iterations: int = 0,
        mode: BenchmarkMode = "uncached",
        provider_kind: ProviderKind = "local_cpu",
        characteristics: dict[str, str | int | float | bool] | None = None,
        setup_each: Callable[[], None] | None = None,
        teardown_each: Callable[[], None] | None = None,
    ) -> BenchmarkSummary:
        if iterations < 1:
            raise ValueError("iterations must be >= 1")
        effective_iterations = 1 if mode == "cold_start" else iterations

        if mode == "warm_cache":
            for _ in range(max(1, warmup_iterations)):
                if setup_each:
                    setup_each()
                try:
                    fn()
                finally:
                    if teardown_each:
                        teardown_each()

        latencies_ms: list[float] = []
        errors = 0

        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()
        tracemalloc.reset_peak()

        suite_start = self._clock_ns()
        for _ in range(effective_iterations):
            if setup_each:
                setup_each()
            op_start = self._clock_ns()
            try:
                fn()
            except (RuntimeError, ValueError, OSError, LookupError):
                errors += 1
            finally:
                elapsed_ms = max(0.0, (self._clock_ns() - op_start) / 1_000_000.0)
                latencies_ms.append(elapsed_ms)
                if teardown_each:
                    teardown_each()
        total_elapsed_sec = max(1e-9, (self._clock_ns() - suite_start) / 1_000_000_000.0)

        _, peak_bytes = tracemalloc.get_traced_memory()
        if not was_tracing:
            tracemalloc.stop()

        sorted_lat = sorted(latencies_ms)
        mean_ms = sum(sorted_lat) / len(sorted_lat)
        simulated = provider_kind == "simulated"
        if simulated:
            disclaimer = (
                "Simulated/fake AI provider measurement: proves orchestration "
                "overhead only and NOT the 2-5s live provider SLA."
            )
        elif provider_kind == "actual":
            disclaimer = (
                "Live external AI provider measurement under bounded workload."
            )
        else:
            disclaimer = (
                "Local deterministic CPU/storage measurement on current hardware."
            )

        return BenchmarkSummary(
            workload_name=workload_name,
            mode=mode,
            provider_kind=provider_kind,
            simulated=simulated,
            disclaimer=disclaimer,
            iterations=effective_iterations,
            errors=errors,
            min_ms=round(sorted_lat[0], 4),
            p50_ms=round(percentile(sorted_lat, 50.0), 4),
            p95_ms=round(percentile(sorted_lat, 95.0), 4),
            p99_ms=round(percentile(sorted_lat, 99.0), 4),
            max_ms=round(sorted_lat[-1], 4),
            mean_ms=round(mean_ms, 4),
            throughput_ops_per_sec=round(effective_iterations / total_elapsed_sec, 2),
            peak_memory_kb=round(peak_bytes / 1024.0, 2),
            workload_characteristics=characteristics or {},
        )
