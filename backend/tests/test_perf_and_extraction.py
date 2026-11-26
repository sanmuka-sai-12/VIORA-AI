import json
import logging
from uuid import uuid4

from viora.ai.public import ChatMessage, FakeAIGateway, Role
from viora.contracts import TenantContext
from viora.perf.public import (
    BenchmarkRunner,
    OperationTracker,
    build_synthetic_corpus,
    percentile,
    run_extraction_baseline,
)


def test_operation_tracker_monotonic_and_sensitive_data_exclusion(caplog) -> None:
    caplog.set_level(logging.INFO, logger="viora.perf.tracker")
    tid = uuid4()
    tick = [10_000_000]  # nanoseconds

    def fake_clock_ns() -> int:
        val = tick[0]
        tick[0] += 5_000_000  # +5 ms per read
        return val

    tracker = OperationTracker("extract_unit", tenant_id=tid, clock_ns=fake_clock_ns)
    with tracker.stage("parse_pdf", input_bytes=4096) as st:
        st.record(output_bytes=1024, cache_status="miss")

    tracker.record_provider_call(
        provider="fake",
        model="fake-model",
        operation="complete",
        latency_ms=12.5,
        retry_count=1,
        input_tokens=100,
        output_tokens=40,
        total_tokens=140,
        simulated=True,
    )
    metrics = tracker.emit_log()
    assert metrics.tenant_id == tid
    assert metrics.total_duration_ms > 0.0
    assert len(metrics.stages) == 1
    assert metrics.stages[0].cache_status == "miss"
    assert metrics.provider_calls[0].simulated is True

    logged = json.loads(caplog.records[-1].getMessage())
    assert set(logged.keys()) == {
        "event",
        "operation",
        "tenant_id",
        "total_duration_ms",
        "stages",
        "provider_calls",
    }


def test_benchmark_runner_modes_percentiles_and_simulated_disclaimer() -> None:
    assert percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50.0) == 3.0
    assert percentile([10.0, 20.0, 30.0, 40.0], 95.0) > 30.0

    runner = BenchmarkRunner()
    ctx = TenantContext(
        actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"institution_admin"})
    )
    fake = FakeAIGateway()

    cold = runner.run(
        "fake_cold",
        lambda: fake.complete(ctx, [ChatMessage(role=Role.user, content="test")]),
        iterations=5,
        mode="cold_start",
        provider_kind="simulated",
    )
    assert cold.iterations == 1
    assert cold.simulated is True
    assert "NOT the 2-5s live provider SLA" in cold.disclaimer

    warm = runner.run(
        "fake_warm",
        lambda: fake.complete(ctx, [ChatMessage(role=Role.user, content="test")]),
        iterations=6,
        warmup_iterations=2,
        mode="warm_cache",
        provider_kind="simulated",
    )
    assert warm.iterations == 6
    assert warm.p99_ms >= warm.p95_ms >= warm.p50_ms >= warm.min_ms
    assert warm.throughput_ops_per_sec > 0.0


def test_extraction_baseline_corpus_and_spike() -> None:
    corpus = build_synthetic_corpus()
    assert len(corpus.plain_text_bytes) > 5000
    assert corpus.multipage_pdf.startswith(b"%PDF-1.4")

    result = run_extraction_baseline(iterations=2)
    assert len(result.benchmarks) >= 10
    assert result.quality_notes["plain_text"]["completeness"] == 1.0
    assert result.quality_notes["multipage_digital_pdf"]["pages"] == 12
    assert result.quality_notes["table_pdf"]["pdfplumber_tables_detected"] >= 1
    assert result.quality_notes["no_extractable_text_pdf"]["requires_ocr_fallback"] is True
