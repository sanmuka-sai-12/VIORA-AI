from typing import Literal
from uuid import UUID

from pydantic import Field

from ..contracts import Contract

CacheStatus = Literal["hit", "miss", "none"]
BenchmarkMode = Literal["cold_start", "warm_cache", "uncached"]
ProviderKind = Literal["simulated", "actual", "local_cpu"]


class StageTiming(Contract):
    name: str = Field(min_length=1, max_length=64)
    duration_ms: float = Field(ge=0.0)
    input_bytes: int = Field(default=0, ge=0)
    output_bytes: int = Field(default=0, ge=0)
    cache_status: CacheStatus = "none"


class ProviderCallMetric(Contract):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    operation: str = Field(min_length=1, max_length=64)
    latency_ms: float = Field(ge=0.0)
    retry_count: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    simulated: bool = False
    succeeded: bool = True
    error_code: str | None = Field(default=None, max_length=64)


class OperationMetrics(Contract):
    operation: str = Field(min_length=1, max_length=64)
    tenant_id: UUID | None = None
    total_duration_ms: float = Field(ge=0.0)
    stages: tuple[StageTiming, ...] = ()
    provider_calls: tuple[ProviderCallMetric, ...] = ()


class BenchmarkSummary(Contract):
    workload_name: str = Field(min_length=1, max_length=128)
    mode: BenchmarkMode
    provider_kind: ProviderKind
    simulated: bool
    disclaimer: str
    iterations: int = Field(ge=1)
    errors: int = Field(default=0, ge=0)
    min_ms: float = Field(ge=0.0)
    p50_ms: float = Field(ge=0.0)
    p95_ms: float = Field(ge=0.0)
    p99_ms: float = Field(ge=0.0)
    max_ms: float = Field(ge=0.0)
    mean_ms: float = Field(ge=0.0)
    throughput_ops_per_sec: float = Field(ge=0.0)
    peak_memory_kb: float = Field(ge=0.0)
    workload_characteristics: dict[str, str | int | float | bool] = Field(
        default_factory=dict
    )
