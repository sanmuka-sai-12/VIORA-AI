import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import monotonic_ns
from uuid import UUID

from .schemas import CacheStatus, OperationMetrics, ProviderCallMetric, StageTiming

logger = logging.getLogger(__name__)


class StageRecorder:
    """Mutable handle for updating stage output sizes and cache hits inside a block."""

    def __init__(self, *, input_bytes: int = 0) -> None:
        self.input_bytes = max(0, input_bytes)
        self.output_bytes = 0
        self.cache_status: CacheStatus = "none"

    def record(
        self,
        *,
        output_bytes: int | None = None,
        cache_status: CacheStatus | None = None,
    ) -> None:
        if output_bytes is not None:
            self.output_bytes = max(0, output_bytes)
        if cache_status is not None:
            self.cache_status = cache_status


class OperationTracker:
    """Monotonic-clock telemetry collector that logs only privacy-safe metadata."""

    def __init__(
        self,
        operation: str,
        *,
        tenant_id: UUID | None = None,
        clock_ns: Callable[[], int] = monotonic_ns,
    ) -> None:
        self.operation = operation
        self.tenant_id = tenant_id
        self._clock_ns = clock_ns
        self._start_ns = self._clock_ns()
        self._stages: list[StageTiming] = []
        self._provider_calls: list[ProviderCallMetric] = []
        self._finished: OperationMetrics | None = None

    @contextmanager
    def stage(self, name: str, *, input_bytes: int = 0) -> Iterator[StageRecorder]:
        recorder = StageRecorder(input_bytes=input_bytes)
        stage_start = self._clock_ns()
        try:
            yield recorder
        finally:
            elapsed_ms = max(0.0, (self._clock_ns() - stage_start) / 1_000_000.0)
            self._stages.append(
                StageTiming(
                    name=name,
                    duration_ms=elapsed_ms,
                    input_bytes=recorder.input_bytes,
                    output_bytes=recorder.output_bytes,
                    cache_status=recorder.cache_status,
                )
            )

    def record_provider_call(
        self,
        *,
        provider: str,
        model: str,
        operation: str,
        latency_ms: float,
        retry_count: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        simulated: bool = False,
        succeeded: bool = True,
        error_code: str | None = None,
    ) -> None:
        self._provider_calls.append(
            ProviderCallMetric(
                provider=provider,
                model=model,
                operation=operation,
                latency_ms=max(0.0, latency_ms),
                retry_count=max(0, retry_count),
                input_tokens=max(0, input_tokens),
                output_tokens=max(0, output_tokens),
                total_tokens=max(0, total_tokens),
                simulated=simulated,
                succeeded=succeeded,
                error_code=error_code,
            )
        )

    def finish(self) -> OperationMetrics:
        if self._finished is None:
            total_ms = max(0.0, (self._clock_ns() - self._start_ns) / 1_000_000.0)
            self._finished = OperationMetrics(
                operation=self.operation,
                tenant_id=self.tenant_id,
                total_duration_ms=total_ms,
                stages=tuple(self._stages),
                provider_calls=tuple(self._provider_calls),
            )
        return self._finished

    def to_log_dict(self) -> dict[str, object]:
        metrics = self.finish()
        return {
            "event": "operation_telemetry",
            "operation": metrics.operation,
            "tenant_id": str(metrics.tenant_id) if metrics.tenant_id else None,
            "total_duration_ms": round(metrics.total_duration_ms, 3),
            "stages": [
                {
                    "name": s.name,
                    "duration_ms": round(s.duration_ms, 3),
                    "input_bytes": s.input_bytes,
                    "output_bytes": s.output_bytes,
                    "cache_status": s.cache_status,
                }
                for s in metrics.stages
            ],
            "provider_calls": [
                {
                    "provider": c.provider,
                    "model": c.model,
                    "operation": c.operation,
                    "latency_ms": round(c.latency_ms, 3),
                    "retry_count": c.retry_count,
                    "input_tokens": c.input_tokens,
                    "output_tokens": c.output_tokens,
                    "total_tokens": c.total_tokens,
                    "simulated": c.simulated,
                    "succeeded": c.succeeded,
                    "error_code": c.error_code,
                }
                for c in metrics.provider_calls
            ],
        }

    def emit_log(self, level: int = logging.INFO) -> OperationMetrics:
        payload = self.to_log_dict()
        logger.log(level, json.dumps(payload, sort_keys=True))
        return self.finish()
