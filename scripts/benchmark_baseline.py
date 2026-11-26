"""Reproducible benchmark runner for Batch 2.1 components.

Measures storage I/O, simulated AI gateway overhead, cold-start/warm-cache/uncached
workloads, and pdfplumber/pypdf extraction baselines across synthetic educational docs.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from viora.ai.public import ChatMessage, FakeAIGateway, Role
from viora.contracts import TenantContext
from viora.perf.public import BenchmarkRunner, OperationTracker, run_extraction_baseline
from viora.storage.public import LocalFilesystemArtifactStore


def run_all_benchmarks() -> dict[str, object]:
    runner = BenchmarkRunner()
    ctx = TenantContext(
        actor_id=uuid4(),
        tenant_id=uuid4(),
        roles=frozenset({"institution_owner"}),
    )

    summaries = []

    # 1. Cold-start vs warm-cache vs uncached local CPU / cache simulation
    cache: dict[str, bytes] = {}
    raw_doc = b"Educational physics chapter digest payload " * 2048  # ~84 KB

    def cold_fn() -> int:
        cache.clear()
        val = raw_doc.upper()
        cache["k"] = val
        return len(val)

    def warm_fn() -> int:
        return len(cache["k"])

    def uncached_fn() -> int:
        return len(raw_doc.upper())

    summaries.append(
        runner.run(
            "payload_transform_cold_start",
            cold_fn,
            iterations=1,
            mode="cold_start",
            provider_kind="local_cpu",
            characteristics={"bytes": len(raw_doc)},
        )
    )
    summaries.append(
        runner.run(
            "payload_transform_warm_cache",
            warm_fn,
            iterations=25,
            warmup_iterations=3,
            mode="warm_cache",
            provider_kind="local_cpu",
            characteristics={"bytes": len(raw_doc)},
        )
    )
    summaries.append(
        runner.run(
            "payload_transform_uncached",
            uncached_fn,
            iterations=25,
            mode="uncached",
            provider_kind="local_cpu",
            characteristics={"bytes": len(raw_doc)},
        )
    )

    # 2. Private storage write + SHA-256 verified read (~100 KB binary)
    sample_blob = b"\x00\x01VIORA_BINARY" * 8192
    with tempfile.TemporaryDirectory() as tmp_dir:
        store = LocalFilesystemArtifactStore(tmp_dir)
        stored_refs = []

        def storage_write() -> None:
            art = store.put_bytes(
                ctx,
                sample_blob,
                kind="source_document",
                resource_id=uuid4(),
                content_type="application/pdf",
            )
            stored_refs.append(art.metadata.ref)

        summaries.append(
            runner.run(
                "storage_immutable_put_100kb",
                storage_write,
                iterations=15,
                mode="uncached",
                provider_kind="local_cpu",
                characteristics={"bytes": len(sample_blob)},
            )
        )
        read_idx = 0

        def storage_read() -> bytes:
            nonlocal read_idx
            ref = stored_refs[read_idx % len(stored_refs)]
            read_idx += 1
            return store.read(ctx, ref)

        summaries.append(
            runner.run(
                "storage_verified_read_100kb",
                storage_read,
                iterations=15,
                mode="uncached",
                provider_kind="local_cpu",
                characteristics={"bytes": len(sample_blob)},
            )
        )

    # 3. Simulated AI gateway (clearly separated from live SLA)
    fake_ai = FakeAIGateway(default_completion="Concept summary response.")
    msgs = [ChatMessage(role=Role.user, content="Summarize kinematics equations.")]
    summaries.append(
        runner.run(
            "ai_gateway_fake_complete",
            lambda: fake_ai.complete(ctx, msgs),
            iterations=25,
            warmup_iterations=2,
            mode="warm_cache",
            provider_kind="simulated",
            characteristics={"messages": 1, "simulated": True},
        )
    )

    # 4. OperationTracker example trace
    tracker = OperationTracker("baseline_suite_probe", tenant_id=ctx.tenant_id)
    with tracker.stage("fake_completion", input_bytes=len(msgs[0].content)) as st:
        res = fake_ai.complete(ctx, msgs)
        st.record(output_bytes=len(res.content), cache_status="miss")
    tracker.record_provider_call(
        provider=res.metadata.provider,
        model=res.model,
        operation="complete",
        latency_ms=res.metadata.latency_ms,
        retry_count=res.metadata.retry_count,
        input_tokens=res.usage.input_tokens,
        output_tokens=res.usage.output_tokens,
        total_tokens=res.usage.total_tokens,
        simulated=res.metadata.simulated,
    )
    sample_trace = tracker.to_log_dict()

    # 5. Extraction baseline
    extraction = run_extraction_baseline(iterations=10)
    summaries.extend(extraction.benchmarks)

    return {
        "benchmarks": [s.model_dump(mode="json") for s in summaries],
        "extraction_quality": extraction.quality_notes,
        "sample_telemetry_log": sample_trace,
    }


def main() -> int:
    report = run_all_benchmarks()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
