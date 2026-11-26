"""Benchmark & evaluation harness for Batch 2.5 adaptive chunking, quality, and concept extraction.

Default mode uses a deterministic test AIGateway + simulated realistic failure/hallucination edge cases
against the CC0 educational corpus and reports decomposed timings without making paid API calls.

Opt-in live Cloudflare Workers AI mode (synthetic corpus only):
  set CLOUDFLARE_ACCOUNT_ID=...
  set CLOUDFLARE_API_TOKEN=...
  python scripts/benchmark_batch_2_5.py --live-cloudflare --confirm-paid-live --model @cf/meta/llama-3.1-8b-instruct-fast
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from viora.ai.public import (
    CLOUDFLARE_MODEL_SHORTLIST,
    DEFAULT_CLOUDFLARE_WORKER_URL,
    AIQuotaExhaustedError,
    AIRequestConfig,
    AIResponseFormatError,
    ChatMessage,
    CloudflareProviderConfig,
    CloudflareWorkerConfig,
    FakeAIGateway,
    build_cloudflare_gateway,
    build_cloudflare_worker_gateway,
    estimate_cloudflare_neurons,
)
from viora.contracts import TenantContext
from viora.knowledge.public import (
    ChunkingConfig,
    ConceptExtractor,
    RawConceptCandidate,
    RawConceptExtractionBatch,
    RawRelationshipCandidate,
    build_concept_extraction_messages,
    chunk_extracted_document,
    estimate_tokens,
    normalize_concept_name,
)
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    DerivedExtractionRecord,
    RapidOcrBackend,
    clamp_source_limits,
)

CORPUS_DIR = REPO_ROOT / "benchmarks" / "educational_corpus"
MANIFEST_PATH = CORPUS_DIR / "ground_truth.json"

# Manual gold concepts for precision/recall evaluation on synthetic CC0 fixtures
GOLD_CONCEPTS: dict[str, set[str]] = {
    "01_markdown_notes": {"kinematics", "average velocity", "constant acceleration"},
    "02_single_column_textbook": {"wave optics", "interference", "huygens wavefront principle"},
    "05_tables_bordered_and_borderless": {"heat capacity ratio", "molar mass", "monatomic gas"},
    "06_math_equations": {"quadratic formula", "euler identity", "gaussian integral"},
    "11_mixed_digital_scanned": {"isothermal process", "conservation of energy", "first law"},
}


def percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


class _EvalDeterministicGateway(FakeAIGateway):
    """Zero-network deterministic evaluator returning known gold + distractor concepts keyed to chunks."""

    def __init__(self, doc_id: str, *, inject_schema_failure_once: bool = False) -> None:
        super().__init__(model_name="fake-eval-deterministic")
        self.doc_id = doc_id
        self.fail_once = inject_schema_failure_once
        self.failed_attempts = 0
        self.calls = 0

    def complete_structured(self, context, messages: list[ChatMessage], response_model, config=None):  # type: ignore[override]
        self.calls += 1
        if self.fail_once and self.calls == 1:
            self.fail_once = False
            self.failed_attempts += 1
            raise AIResponseFormatError("Simulated malformed JSON response on chunk 1")
        user_txt = messages[-1].content
        ev_ids: list[str] = []
        pos = 0
        while True:
            idx = user_txt.find("[ev:", pos)
            if idx == -1:
                break
            end = user_txt.find("]", idx)
            if end == -1:
                break
            for eid in user_txt[idx + 4 : end].split(","):
                if eid.strip() and eid.strip() not in ev_ids:
                    ev_ids.append(eid.strip())
            pos = end + 1

        golds = sorted(GOLD_CONCEPTS.get(self.doc_id, {"educational concept"}))
        cands = [
            RawConceptCandidate(
                name=name.title(),
                canonical_summary=f"Extracted grounded summary for {name}.",
                grounding="source_explicit" if ev_ids else "uncertain",
                evidence_ids=ev_ids[:1],
                learning_objectives=[f"Apply {name} accurately."],
            )
            for name in golds
        ]
        # Add one unsupported hallucinated citation on 05_tables_bordered_and_borderless to measure rejection
        if self.doc_id == "05_tables_bordered_and_borderless" and len(ev_ids) >= 1:
            cands.append(
                RawConceptCandidate(
                    name="Unverified Claim",
                    canonical_summary="Claim without matching evidence.",
                    grounding="source_explicit",
                    evidence_ids=["ev-does-not-exist"],
                )
            )
        rels = []
        if len(cands) >= 2:
            rels.append(
                RawRelationshipCandidate(
                    source_concept=cands[0].name,
                    relation_type="applies_to",
                    target_concept=cands[1].name,
                    grounding="source_explicit" if ev_ids else "uncertain",
                    evidence_ids=ev_ids[:1],
                )
            )
        self.structured_payloads[response_model] = RawConceptExtractionBatch(
            section_summary=f"Summary for {self.doc_id}",
            concepts=cands,
            relationships=rels,
        )
        return super().complete_structured(context, messages, response_model, config)


def _extract_corpus(doc_ids: list[str]) -> dict[str, tuple[DerivedExtractionRecord, float]]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    limits = clamp_source_limits()
    ocr = RapidOcrBackend()
    extractor = AdaptiveDocumentExtractor(
        ocr_backend=ocr if ocr.is_available() else None, page_workers=1
    )
    out: dict[str, tuple[DerivedExtractionRecord, float]] = {}
    for doc_id in doc_ids:
        item = next(d for d in manifest["documents"] if d["doc_id"] == doc_id)
        raw = (CORPUS_DIR / item["relative_path"]).read_bytes()
        t0 = time.monotonic_ns()
        doc = extractor.extract(
            tenant_id=uuid4(),
            source_id=uuid4(),
            version_id=uuid4(),
            source_version=1,
            source_sha256=item["sha256"],
            input_format=item["input_format"],
            title=item["filename"],
            raw_bytes=raw,
            limits=limits,
        )
        doc_ms = (time.monotonic_ns() - t0) / 1_000_000.0
        derived = DerivedExtractionRecord(
            extraction_id=uuid4(),
            tenant_id=doc.tenant_id,
            source_id=doc.source_id,
            version_id=doc.version_id,
            source_sha256=doc.source_sha256,
            extraction_version=1,
            document=doc,
            created_at=doc.created_at,
        )
        out[doc_id] = (derived, doc_ms)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch 2.5 chunking & concept extraction evaluation")
    parser.add_argument("--live-cloudflare", action="store_true", help="Enable real Cloudflare Workers AI inference")
    parser.add_argument("--confirm-paid-live", action="store_true", help="Explicit confirmation for live API calls")
    parser.add_argument(
        "--transport",
        choices=("cloudflare_direct", "cloudflare_worker"),
        default="cloudflare_direct",
        help="Explicit live Cloudflare transport (direct REST API or deployed Worker)",
    )
    parser.add_argument("--worker-url", default=DEFAULT_CLOUDFLARE_WORKER_URL, help="Trusted Worker URL")
    parser.add_argument("--max-requests", type=int, default=1, help="Bounded max live requests (1 for smoke, <= 3 for validation)")
    parser.add_argument("--model", default="@cf/meta/llama-3.1-8b-instruct-fast")
    args = parser.parse_args(argv)

    if args.live_cloudflare and not args.confirm_paid_live:
        raise SystemExit("Live Cloudflare evaluation requires explicit --confirm-paid-live confirmation")
    if args.live_cloudflare and (args.max_requests < 1 or args.max_requests > 3):
        raise SystemExit("Live free-tier validation strictly bounds --max-requests between 1 and 3")

    doc_ids = list(GOLD_CONCEPTS.keys()) + ["03_multicolumn_scientific", "04_code_blocks", "07_scanned_printed_page_png"]
    extracted = _extract_corpus(doc_ids)

    # Synthetic oversized 3,000-token STEM handbook for hierarchical vs single-pass stress comparison
    oversized_txt = "\n\n".join(
        f"## Module {i}: Classical Mechanics & Thermodynamics Part {i}\n\n"
        + ("Work done equals the path integral of force over displacement F dot dr. " * 26)
        for i in range(1, 9)
    ).encode("utf-8")
    ext = AdaptiveDocumentExtractor()
    over_doc = ext.extract(
        tenant_id=uuid4(),
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="c" * 64,
        input_format="typed_notes",
        title="Oversized Mechanics Module",
        raw_bytes=oversized_txt,
        limits=clamp_source_limits(),
    )
    over_derived = DerivedExtractionRecord(
        extraction_id=uuid4(),
        tenant_id=over_doc.tenant_id,
        source_id=over_doc.source_id,
        version_id=over_doc.version_id,
        source_sha256=over_doc.source_sha256,
        extraction_version=1,
        document=over_doc,
        created_at=over_doc.created_at,
    )

    chunk_benchmarks: list[dict] = []
    for label, derived_rec in [*[(k, extracted[k][0]) for k in doc_ids], ("oversized_synthetic_module", over_derived)]:
        for mode in ("single_pass", "hierarchical"):
            plan = chunk_extracted_document(
                derived_rec.document,
                config=ChunkingConfig(single_pass_max_tokens=1600, chunk_max_tokens=900, overlap_tokens=64),
                force_mode=mode,  # type: ignore[arg-type]
            )
            sum_chunk_tok = sum(c.estimated_tokens for c in plan.chunks)
            denom = max(1, len(plan.chunks) * plan.budget_tokens)
            chunk_benchmarks.append(
                {
                    "document": label,
                    "mode": mode,
                    "chunks": len(plan.chunks),
                    "total_est_tokens": plan.total_estimated_tokens,
                    "token_utilization": round(sum_chunk_tok / denom, 4),
                    "coverage_ratio": plan.coverage_ratio,
                    "duplicate_ratio": plan.duplicate_ratio,
                    "chunking_ms": plan.chunking_ms,
                }
            )

    # Concept extraction evaluation (single_pass vs hierarchical on GOLD_CONCEPTS subset)
    live_gw = None
    live_mode = bool(args.live_cloudflare)
    live_requests_remaining = args.max_requests if live_mode else 999
    if live_mode:
        if args.transport == "cloudflare_worker":
            secret = os.environ.get("CLOUDFLARE_WORKER_SECRET", "").strip()
            if not secret:
                raise SystemExit("CLOUDFLARE_WORKER_SECRET must be set for --transport cloudflare_worker")
            live_gw = build_cloudflare_worker_gateway(
                CloudflareWorkerConfig(
                    worker_url=args.worker_url,
                    secret=SecretStr(secret),
                    model=args.model,
                )
            )
        else:
            acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
            tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
            if not acct or not tok:
                raise SystemExit("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must be set for --live-cloudflare")
            live_gw = build_cloudflare_gateway(
                CloudflareProviderConfig(account_id=acct, api_token=SecretStr(tok), model=args.model)
            )
        # Pre-flight Neuron budget estimate before issuing any live call
        sample_rec, _ = extracted["01_markdown_notes"]
        sample_plan = chunk_extracted_document(sample_rec.document, force_mode="single_pass")
        sample_msgs = build_concept_extraction_messages(
            sample_plan.chunks[0],
            document_title=sample_rec.document.title,
            input_format=sample_rec.document.input_format,
        )
        schema_chars = len(json.dumps(RawConceptExtractionBatch.model_json_schema()))
        est_in_tok = sum(estimate_tokens(m.content) for m in sample_msgs) + estimate_tokens(" " * schema_chars)
        est_out_tok = 500
        neuron_est = estimate_cloudflare_neurons(
            args.model,
            input_tokens=est_in_tok * args.max_requests,
            output_tokens=est_out_tok * args.max_requests,
        )
        print(json.dumps({"pre_flight_neuron_estimate": neuron_est}), file=sys.stderr)

    ctx = TenantContext(actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"subject_teacher"}))
    mode_results: dict[str, list[dict]] = {"single_pass": [], "hierarchical": []}
    e2e_latencies: dict[str, list[float]] = {"single_pass": [], "hierarchical": []}
    model_caps = CLOUDFLARE_MODEL_SHORTLIST.get(args.model)

    for mode in ("single_pass", "hierarchical"):
        if live_mode and live_requests_remaining <= 0:
            break
        for doc_id, gold in GOLD_CONCEPTS.items():
            if live_mode and live_requests_remaining <= 0:
                break
            derived_rec, doc_ms = extracted[doc_id]
            gw = live_gw if live_gw is not None else _EvalDeterministicGateway(doc_id)
            req_cfg = AIRequestConfig(
                max_tokens=500 if (live_mode and args.transport == "cloudflare_worker") else (600 if live_mode else 900),
                temperature=0.0,
                timeout_seconds=20.0,
                max_retries=0 if live_mode else 1,
            )
            c_ext = ConceptExtractor(
                gw,
                chunk_config=ChunkingConfig(single_pass_max_tokens=1600, chunk_max_tokens=300, overlap_tokens=32),
                request_config=req_cfg,
                max_inference_requests=live_requests_remaining if live_mode else 8,
            )
            try:
                res = c_ext.extract_concepts(context=ctx, derived=derived_rec, force_mode=mode)  # type: ignore[arg-type]
            except AIQuotaExhaustedError as exc:
                raise SystemExit(f"Cloudflare free-tier quota exhausted; stopping immediately: {exc}") from None
            if live_mode:
                live_requests_remaining -= res.inference_requests
            kg = res.knowledge
            pred_supported = {
                c.normalized_name
                for c in kg.concepts
                if c.grounding != "unsupported"
            }
            # Token-overlap matching for natural phrasing variations in live models
            matched_gold = {
                g
                for g in gold
                if any(
                    all(tok in p for tok in normalize_concept_name(g).split()) or g == p
                    for p in pred_supported
                )
            }
            matched_pred = {
                p
                for p in pred_supported
                if any(
                    all(tok in p for tok in normalize_concept_name(g).split()) or g == p
                    for g in gold
                )
            }
            prec = round(len(matched_pred) / max(1, len(pred_supported)), 4)
            rec = round(len(matched_gold) / max(1, len(gold)), 4)
            total_ev = sum(len(c.evidence_bindings) for c in kg.concepts)
            verified_ev = sum(1 for c in kg.concepts for b in c.evidence_bindings if b.verified)
            est_cost_usd = None
            if model_caps and model_caps.usd_per_1m_input_tokens and model_caps.usd_per_1m_output_tokens:
                est_cost_usd = round(
                    (res.input_tokens * model_caps.usd_per_1m_input_tokens / 1_000_000.0)
                    + (res.output_tokens * model_caps.usd_per_1m_output_tokens / 1_000_000.0),
                    6,
                )
            total_ms = round(doc_ms + res.chunking_ms + res.inference_ms + res.validation_ms, 3)
            e2e_latencies[mode].append(total_ms)
            mode_results[mode].append(
                {
                    "document": doc_id,
                    "doc_extract_ms": round(doc_ms, 3),
                    "chunking_ms": res.chunking_ms,
                    "inference_ms": res.inference_ms,
                    "validation_ms": res.validation_ms,
                    "e2e_ms": total_ms,
                    "inference_requests": res.inference_requests,
                    "input_tokens": res.input_tokens,
                    "output_tokens": res.output_tokens,
                    "concepts": kg.validation.total_concepts,
                    "precision": prec,
                    "recall": rec,
                    "evidence_accuracy": round(verified_ev / max(1, total_ev), 4) if total_ev else 0.0,
                    "unsupported_claims": kg.validation.unsupported_count,
                    "rejected_evidence_refs": kg.validation.invalid_evidence_refs_rejected,
                    "duplicate_merges": kg.validation.duplicate_merges_count,
                    "estimated_cost_usd": est_cost_usd,
                }
            )

    summary = {
        "simulated_provider": not live_mode,
        "provider": args.model if live_mode else "fake-eval-deterministic",
        "disclaimer": (
            "Zero-network deterministic evaluation (AI network latency is simulated, NOT live Cloudflare edge latency)."
            if not live_mode
            else f"Live Cloudflare Workers AI evaluation using model={args.model} on synthetic CC0 corpus."
        ),
        "chunking_benchmarks": chunk_benchmarks,
        "concept_eval_by_mode": {
            mode: {
                "samples": len(rows),
                "e2e_p50_ms": round(percentile(sorted(e2e_latencies[mode]), 50.0), 2),
                "e2e_p95_ms": round(percentile(sorted(e2e_latencies[mode]), 95.0), 2),
                "mean_precision": round(sum(r["precision"] for r in rows) / len(rows), 4),
                "mean_recall": round(sum(r["recall"] for r in rows) / len(rows), 4),
                "total_inference_requests": sum(r["inference_requests"] for r in rows),
                "total_tokens": sum(r["input_tokens"] + r["output_tokens"] for r in rows),
                "rows": rows,
            }
            for mode, rows in mode_results.items()
        },
        "bounded_interactive_recommendation": {
            "target_e2e_seconds": "2-5s",
            "single_pass_input_budget_tokens": 1600,
            "hierarchical_chunk_budget_tokens": 900,
            "recommended_default_model": "@cf/meta/llama-3.1-8b-instruct-fast",
            "recommended_lowest_neuron_model": "@cf/ibm-granite/granite-4.0-h-micro",
            "rationale": (
                "Keep interactive concept extraction <= 1600 input tokens (1 LLM call + 200-500 output tokens) "
                "or bounded <= 3 section chunks asynchronously to stay within the 2-5s objective alongside document parsing."
            ),
        },
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
