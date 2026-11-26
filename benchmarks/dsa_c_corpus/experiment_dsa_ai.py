"""Controlled 3-request DSA AI knowledge experiment (dry-run/preflight by default; strict Cloudflare Worker opt-in)."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import SecretStr

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from viora.ai.cloudflare import FREE_TIER_DAILY_NEURONS
from viora.ai.openai_adapter import _compact_json_schema
from viora.ai.public import (
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_CLOUDFLARE_WORKER_URL,
    AIError,
    AIQuotaExhaustedError,
    AIRequestConfig,
    ChatMessage,
    CloudflareWorkerConfig,
    FakeAIGateway,
    Role,
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
)
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    DerivedExtractionRecord,
    clamp_source_limits,
)

DSA_DIR = Path(__file__).resolve().parent
WORKER_MAX_BODY_BYTES = 16 * 1024  # 16,384 bytes enforced by cloudflare/viora-worker/index.js
DEFAULT_RESULTS_PATH = DSA_DIR / "ai_experiment_results.json"

RUBRIC = {
    "scale_note": "Independent deterministic rubric evaluation (0-2 per dimension, max 10, minus unsupported-claim penalty).",
    "dimensions": [
        {"name": "factual_correctness", "max_score": 2, "criteria": "Accurate technical statements with zero semantic contradictions."},
        {"name": "complexity_correctness", "max_score": 2, "criteria": "Accurately distinguishes O(V+E) under guard vs redundant scans / queue expansion without duplicate-pop guard."},
        {"name": "conceptual_completeness", "max_score": 2, "criteria": "Explains duplicate frontier enqueuing and queue-once invariant breakdown."},
        {"name": "preconditions_and_limitations", "max_score": 2, "criteria": "Explicitly identifies unweighted-edge assumption and queue-capacity preconditions."},
        {"name": "c_implementation_and_memory_safety", "max_score": 2, "criteria": "Identifies fixed-size int q[V] out-of-bounds buffer overflow undefined behavior in C."},
        {"name": "unsupported_claims", "penalty_per_claim": -1, "criteria": "Deduct 1 per claim not supported by standard C/DSA facts or supplied passages."},
    ],
}


def _load_repo_dotenv(dotenv_path: Path) -> dict[str, str]:
    """Read key=value pairs from .env without printing secrets or mutating .env on disk."""
    loaded: dict[str, str] = {}
    if not dotenv_path.is_file():
        return loaded
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1]
        loaded[key] = val
    return loaded


def _build_dry_gateway(doc_evidence_id: str) -> FakeAIGateway:
    gw = FakeAIGateway(model_name="fake-dry-run")
    gw.structured_payloads[RawConceptExtractionBatch] = RawConceptExtractionBatch(
        section_summary="Dynamic arrays double capacity on overflow; circular queues wrap tail via (head + count) % CAP.",
        concepts=[
            RawConceptCandidate(
                name="Dynamic Array Doubling",
                canonical_summary="Doubling array capacity on overflow keeps amortized push complexity at O(1).",
                grounding="source_explicit",
                evidence_ids=[doc_evidence_id] if doc_evidence_id else [],
                learning_objectives=["Derive amortized O(1) push cost under capacity doubling."],
            ),
            RawConceptCandidate(
                name="Circular Queue Tail Indexing",
                canonical_summary="Computes tail index (head + count) % CAP in a fixed ring buffer.",
                grounding="source_explicit",
                evidence_ids=[doc_evidence_id] if doc_evidence_id else [],
                prerequisites=["Dynamic Array Doubling"],
                learning_objectives=["Compute circular tail offsets modulo capacity."],
            ),
        ],
        relationships=[
            RawRelationshipCandidate(
                source_concept="Dynamic Array Doubling",
                relation_type="prerequisite_of",
                target_concept="Circular Queue Tail Indexing",
                grounding="source_synthesized",
                evidence_ids=[doc_evidence_id] if doc_evidence_id else [],
            )
        ],
    )
    return gw


def _select_eval_payloads() -> dict[str, Any]:
    questions = json.loads((DSA_DIR / "eval_questions.json").read_text(encoding="utf-8"))["questions"]
    concepts = {
        c["concept_id"]: c
        for c in json.loads((DSA_DIR / "corpus_concepts.json").read_text(encoding="utf-8"))["concepts"]
    }
    q = next(x for x in questions if x["question_id"] == "eval-reas-02")
    # Held-out prompt ONLY (expected_answer and evaluation_criteria remain strictly outside prompts)
    prompt_q = q["prompt"]

    bfs = concepts["dsa-c-graph-adj-bfs"]
    ring = concepts["dsa-c-circular-ring-queue"]
    grounded_context = (
        f"[Concept: {bfs['concept_id']} — {bfs['title']}]\n"
        f"Definition: {bfs['definition']}\n"
        f"Working Principle: {bfs['working_principle']}\n"
        f"Time/Space: worst={bfs['time_complexity']['worst']}; aux_space={bfs['auxiliary_space_complexity']}\n"
        f"Preconditions: {bfs['preconditions_and_limitations']}\n"
        f"Common Mistakes: {'; '.join(bfs['common_mistakes'])}\n"
        f"C Example: {bfs['c_example']}\n\n"
        f"[Concept: {ring['concept_id']} — {ring['title']}]\n"
        f"Preconditions: {ring['preconditions_and_limitations']}\n"
        f"C Example: {ring['c_example']}"
    )

    qa_system = (
        "Answer the computer science and C programming question concisely and rigorously.\n"
        "Cover conceptual correctness, time/space complexity, preconditions, and C memory/undefined-behavior implications.\n"
        "Stay within 500 tokens. Do not invent facts you are uncertain about."
    )
    msg_b = [
        ChatMessage(role=Role.system, content=qa_system),
        ChatMessage(role=Role.user, content=f"Question:\n{prompt_q}"),
    ]
    msg_c = [
        ChatMessage(role=Role.system, content=qa_system),
        ChatMessage(role=Role.user, content=f"Course Passages:\n{grounded_context}\n\nQuestion:\n{prompt_q}"),
    ]

    note_path = DSA_DIR / "fixtures" / "01_dsa_notes.md"
    raw_bytes = note_path.read_bytes()
    doc = AdaptiveDocumentExtractor().extract(
        tenant_id=uuid4(),
        source_id=uuid4(),
        version_id=uuid4(),
        source_version=1,
        source_sha256="d" * 64,
        input_format="typed_notes",
        title="01_dsa_notes.md",
        raw_bytes=raw_bytes,
        limits=clamp_source_limits(),
    )
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
    plan = chunk_extracted_document(
        doc, config=ChunkingConfig(single_pass_max_tokens=1600, chunk_max_tokens=900, overlap_tokens=64)
    )
    msgs_a = build_concept_extraction_messages(plan.chunks[0], document_title=doc.title, input_format=doc.input_format)
    return {
        "question_id": q["question_id"],
        "question_prompt": prompt_q,
        "qa_system": qa_system,
        "derived_a": derived,
        "plan_a": plan,
        "messages_a": msgs_a,
        "messages_b": msg_b,
        "messages_c": msg_c,
    }


def _serialize_request_metrics(
    *,
    model: str,
    messages: list[ChatMessage],
    cfg: AIRequestConfig,
    structured_schema: bool,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": m.role.value, "content": m.content} for m in messages],
    }
    if structured_schema:
        schema_json = _compact_json_schema(RawConceptExtractionBatch.model_json_schema())
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": schema_json,
        }
    if cfg.max_tokens is not None:
        body["max_tokens"] = cfg.max_tokens
    if cfg.temperature is not None:
        body["temperature"] = cfg.temperature

    compact_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    wire_json = json.dumps(body, ensure_ascii=False)
    compact_bytes = len(compact_json.encode("utf-8"))
    wire_bytes = len(wire_json.encode("utf-8"))
    est_tokens_full_request = estimate_tokens(wire_json)
    est_tokens_messages_only = estimate_tokens("\n".join(m.content for m in messages))
    return {
        "wire_bytes": wire_bytes,
        "compact_bytes": compact_bytes,
        "within_16kb_worker_ceiling": wire_bytes < WORKER_MAX_BODY_BYTES,
        "worker_ceiling_bytes": WORKER_MAX_BODY_BYTES,
        "headroom_bytes": WORKER_MAX_BODY_BYTES - wire_bytes,
        "estimated_input_tokens_full_serialized_request": est_tokens_full_request,
        "estimated_input_tokens_messages_only": est_tokens_messages_only,
        "worst_case_neurons": estimate_cloudflare_neurons(
            model, input_tokens=est_tokens_full_request, output_tokens=cfg.max_tokens or 500
        ),
    }


def _check_worker_health(worker_url: str, *, require_live_ready: bool) -> dict[str, Any]:
    parsed = urlsplit(worker_url)
    health_url = f"{parsed.scheme}://{parsed.netloc}/health"
    info: dict[str, Any] = {
        "health_url": health_url,
        "reachable": False,
        "status_code": None,
        "body": None,
        "remote_quota_balance_note": (
            "Worker GET /health checks config/secret readiness with inference=false (0 Neurons). "
            "Cloudflare Workers AI bindings do not expose remaining daily Neuron balance; "
            "account-level billing API credentials are not loaded in .env, so runtime fails closed on HTTP 429 AI_QUOTA_EXCEEDED."
        ),
    }
    try:
        resp = httpx.get(health_url, timeout=10.0)
        info["status_code"] = resp.status_code
        try:
            info["body"] = resp.json()
        except ValueError:
            info["body"] = resp.text[:300]
        info["reachable"] = resp.status_code == 200 and isinstance(info["body"], dict) and info["body"].get("status") == "ok"
    except httpx.HTTPError as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    if require_live_ready and not info["reachable"]:
        raise SystemExit(f"Worker /health preflight failed: {json.dumps(info)}")
    return info


class _WorkerSpyRecorder:
    """Wraps OpenAI SDK client.chat.completions.create in-place to enforce hard cap & capture raw metadata."""

    def __init__(self, orig_create: Any, max_requests: int) -> None:
        self.orig_create = orig_create
        self.max_requests = max_requests
        self.attempts = 0
        self.captures: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.attempts >= self.max_requests:
            raise RuntimeError(f"Hard live inference cap reached ({self.max_requests}); refusing extra outbound attempt")
        self.attempts += 1

        wire_payload = {k: v for k, v in kwargs.items() if k != "timeout"}
        wire_json = json.dumps(wire_payload, ensure_ascii=False)
        wire_bytes = len(wire_json.encode("utf-8"))
        if wire_bytes >= WORKER_MAX_BODY_BYTES:
            raise RuntimeError(f"Serialized payload {wire_bytes} bytes >= Worker ceiling {WORKER_MAX_BODY_BYTES} bytes")

        t0 = time.monotonic_ns()
        capture: dict[str, Any] = {
            "attempt_number": self.attempts,
            "wire_bytes": wire_bytes,
            "est_input_tokens_serialized": estimate_tokens(wire_json),
            "finish_reason": None,
            "raw_content": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "worker_request_id": None,
            "worker_latency_ms": None,
            "client_latency_ms": None,
            "error": None,
        }
        self.captures.append(capture)
        try:
            resp = self.orig_create(*args, **kwargs)
        except Exception as exc:
            capture["client_latency_ms"] = round((time.monotonic_ns() - t0) / 1_000_000.0, 2)
            capture["error"] = f"{type(exc).__name__}: {exc}"
            raise

        capture["client_latency_ms"] = round((time.monotonic_ns() - t0) / 1_000_000.0, 2)
        try:
            dumped = resp.model_dump() if hasattr(resp, "model_dump") else {}
        except (TypeError, ValueError, AttributeError):
            dumped = {}
        choices = getattr(resp, "choices", None) or []
        if choices:
            c0 = choices[0]
            capture["finish_reason"] = getattr(c0, "finish_reason", None)
            msg = getattr(c0, "message", None)
            capture["raw_content"] = getattr(msg, "content", None) if msg is not None else None
        usage = getattr(resp, "usage", None)
        if usage is not None:
            capture["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
            capture["completion_tokens"] = getattr(usage, "completion_tokens", None)
            capture["total_tokens"] = getattr(usage, "total_tokens", None)
        capture["worker_request_id"] = dumped.get("request_id") or getattr(resp, "request_id", None)
        capture["worker_latency_ms"] = dumped.get("latency_ms") or getattr(resp, "latency_ms", None)
        return resp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded 3-request DSA AI knowledge experiment (Workers Free plan)")
    parser.add_argument("--preflight", action="store_true", help="Run zero-inference preflight checks (/health + serialized payload sizes)")
    parser.add_argument("--live-cloudflare", action="store_true", help="Execute live inference via cloudflare_worker (requires --confirm-free-tier-live)")
    parser.add_argument("--confirm-free-tier-live", "--confirm-live", dest="confirm_free_tier_live", action="store_true", help="Explicit approval for up to 3 free-tier Cloudflare Worker requests")
    parser.add_argument("--max-requests", type=int, default=3)
    parser.add_argument("--reuse-a-from", type=Path, default=None, help="Preserve already-executed Request A result and run only B & C")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to save sanitized JSON report")
    args = parser.parse_args(argv)

    if args.max_requests < 1 or args.max_requests > 3:
        raise SystemExit("Hard safety cap: --max-requests must be in 1..3")
    if args.live_cloudflare and not args.confirm_free_tier_live:
        raise SystemExit("Refusing live requests without explicit --confirm-free-tier-live (or --confirm-live) confirmation")

    dotenv = _load_repo_dotenv(REPO_ROOT / ".env")
    worker_url = (os.environ.get("CLOUDFLARE_WORKER_URL") or dotenv.get("CLOUDFLARE_WORKER_URL") or DEFAULT_CLOUDFLARE_WORKER_URL).strip()
    model_id = (os.environ.get("CLOUDFLARE_AI_MODEL") or dotenv.get("CLOUDFLARE_AI_MODEL") or DEFAULT_CLOUDFLARE_MODEL).strip()
    worker_secret = (os.environ.get("CLOUDFLARE_WORKER_SECRET") or dotenv.get("CLOUDFLARE_WORKER_SECRET") or "").strip()

    prep = _select_eval_payloads()
    cfg = AIRequestConfig(
        max_tokens=500,
        temperature=0.0,
        timeout_seconds=25.0,
        total_deadline_seconds=30.0,
        max_retries=0,
    )

    preflight_requests = {
        "A_concept_extraction": _serialize_request_metrics(
            model=model_id, messages=prep["messages_a"], cfg=cfg, structured_schema=True
        ),
        "B_closed_book_qa": _serialize_request_metrics(
            model=model_id, messages=prep["messages_b"], cfg=cfg, structured_schema=False
        ),
        "C_corpus_grounded_qa": _serialize_request_metrics(
            model=model_id, messages=prep["messages_c"], cfg=cfg, structured_schema=False
        ),
    }
    worst_case_total_neurons = round(
        sum(float(v["worst_case_neurons"]["estimated_neurons"] or 0.0) for v in preflight_requests.values()),
        4,
    )
    all_within_ceiling = all(v["within_16kb_worker_ceiling"] for v in preflight_requests.values())

    assert prep["messages_b"][0].content == prep["messages_c"][0].content, "System instructions for B and C must be identical!"
    assert cfg.max_tokens == 500 and cfg.temperature == 0.0 and cfg.max_retries == 0

    health_status = _check_worker_health(worker_url, require_live_ready=args.live_cloudflare) if (args.preflight or args.live_cloudflare) else None

    preflight_report = {
        "transport": "cloudflare_worker",
        "worker_url": worker_url,
        "model": model_id,
        "worker_secret_configured": bool(worker_secret),
        "b_and_c_system_prompt_identical": prep["messages_b"][0].content == prep["messages_c"][0].content,
        "worker_health": health_status,
        "all_payloads_within_16kb": all_within_ceiling,
        "requests": preflight_requests,
        "worst_case_total_neurons": worst_case_total_neurons,
        "free_tier_daily_neurons": FREE_TIER_DAILY_NEURONS,
        "worst_case_pct_of_daily_free_tier": round((worst_case_total_neurons / FREE_TIER_DAILY_NEURONS) * 100.0, 4),
    }

    if args.preflight and not args.live_cloudflare:
        out_text = json.dumps({"mode": "PREFLIGHT_ONLY", "preflight": preflight_report}, indent=2)
        print(out_text)
        return 0 if all_within_ceiling and (health_status is None or health_status["reachable"]) else 1

    if args.live_cloudflare:
        if not worker_secret:
            raise SystemExit("CLOUDFLARE_WORKER_SECRET is missing in environment and .env")
        if not all_within_ceiling:
            raise SystemExit("Preflight aborted live execution: one or more requests exceed 16 KB Worker body ceiling")
        gw: Any = build_cloudflare_worker_gateway(
            CloudflareWorkerConfig(
                worker_url=worker_url,
                secret=SecretStr(worker_secret),
                model=model_id,
            )
        )
        spy = _WorkerSpyRecorder(gw.client.chat.completions.create, max_requests=args.max_requests)
        gw.client.chat.completions.create = spy
    else:
        ev0 = prep["derived_a"].document.evidence_references[0].evidence_id if prep["derived_a"].document.evidence_references else ""
        gw = _build_dry_gateway(ev0)
        spy = None

    ctx = TenantContext(actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"subject_teacher"}))
    report: dict[str, Any] = {
        "mode": "LIVE_CLOUDFLARE_WORKER" if args.live_cloudflare else "DRY_RUN_SIMULATED",
        "transport": "cloudflare_worker",
        "worker_url": worker_url,
        "model": model_id,
        "generation_settings": {
            "max_tokens": cfg.max_tokens,
            "temperature": cfg.temperature,
            "max_retries": cfg.max_retries,
        },
        "max_outbound_requests_allowed": args.max_requests,
        "outbound_attempts_executed": 0,
        "question_id": prep["question_id"],
        "question_prompt": prep["question_prompt"],
        "shared_qa_system_prompt": prep["qa_system"],
        "preflight": preflight_report,
        "rubric": RUBRIC,
        "requests": {},
        "actual_total_neurons": 0.0,
        "pct_of_daily_free_tier": 0.0,
    }

    stop_experiment = False
    prior_attempts = 0

    if args.reuse_a_from is not None:
        prior_data = json.loads(args.reuse_a_from.read_text(encoding="utf-8"))
        prior_a = prior_data["requests"]["A_concept_extraction"]
        prior_attempts = int(prior_data.get("outbound_attempts_executed", 1))
        if prior_attempts + args.max_requests > 3:
            raise SystemExit(f"Aggregate attempt budget exceeded: prior {prior_attempts} + max_requests {args.max_requests} > 3")
        report["requests"]["A_concept_extraction"] = prior_a
    else:
        cap_before = len(spy.captures) if spy else 0
        try:
            res_a = ConceptExtractor(gw, request_config=cfg, max_inference_requests=1).extract_concepts(
                context=ctx, derived=prep["derived_a"], force_mode="single_pass"
            )
            cap_a = spy.captures[-1] if (spy and len(spy.captures) > cap_before) else {}
            in_tok_a = res_a.input_tokens or (cap_a.get("prompt_tokens") or 0)
            out_tok_a = res_a.output_tokens or (cap_a.get("completion_tokens") or 0)
            neur_a = estimate_cloudflare_neurons(model_id, input_tokens=in_tok_a, output_tokens=out_tok_a)
            report["requests"]["A_concept_extraction"] = {
                "structured_output_valid": res_a.knowledge.validation.total_concepts > 0,
                "finish_reason": cap_a.get("finish_reason"),
                "truncated": cap_a.get("finish_reason") == "length",
                "tokens": {
                    "input_tokens": in_tok_a,
                    "output_tokens": out_tok_a,
                    "total_tokens": res_a.total_tokens or (cap_a.get("total_tokens") or (in_tok_a + out_tok_a)),
                },
                "latency_ms": cap_a.get("client_latency_ms") or round(res_a.inference_ms, 2),
                "worker_latency_ms": cap_a.get("worker_latency_ms"),
                "worker_request_id": cap_a.get("worker_request_id"),
                "wire_bytes": cap_a.get("wire_bytes") or preflight_requests["A_concept_extraction"]["wire_bytes"],
                "neurons": neur_a,
                "validation": res_a.knowledge.validation.model_dump(mode="json"),
                "concepts": [c.model_dump(mode="json") for c in res_a.knowledge.concepts],
                "relationships": [r.model_dump(mode="json") for r in res_a.knowledge.relationships],
                "raw_response": cap_a.get("raw_content"),
                "error": None,
            }
        except (AIError, httpx.HTTPError, RuntimeError, ValueError) as exc:
            cap_a = spy.captures[-1] if (spy and len(spy.captures) > cap_before) else {}
            in_tok_a = cap_a.get("prompt_tokens") or 0
            out_tok_a = cap_a.get("completion_tokens") or 0
            neur_a = estimate_cloudflare_neurons(model_id, input_tokens=in_tok_a, output_tokens=out_tok_a)
            report["requests"]["A_concept_extraction"] = {
                "structured_output_valid": False,
                "finish_reason": cap_a.get("finish_reason"),
                "truncated": cap_a.get("finish_reason") == "length",
                "tokens": {
                    "input_tokens": in_tok_a,
                    "output_tokens": out_tok_a,
                    "total_tokens": cap_a.get("total_tokens") or (in_tok_a + out_tok_a),
                },
                "latency_ms": cap_a.get("client_latency_ms"),
                "worker_latency_ms": cap_a.get("worker_latency_ms"),
                "worker_request_id": cap_a.get("worker_request_id"),
                "wire_bytes": cap_a.get("wire_bytes") or preflight_requests["A_concept_extraction"]["wire_bytes"],
                "neurons": neur_a,
                "raw_response": cap_a.get("raw_content"),
                "error": f"{type(exc).__name__}: {exc}",
            }
            if isinstance(exc, (AIQuotaExhaustedError, AIError)) and "AIResponseFormatError" not in type(exc).__name__:
                stop_experiment = True

    for key, msgs in (("B_closed_book_qa", prep["messages_b"]), ("C_corpus_grounded_qa", prep["messages_c"])):
        if stop_experiment:
            report["requests"][key] = {"skipped_due_to_prior_provider_error": True}
            continue
        cap_before = len(spy.captures) if spy else 0
        if args.live_cloudflare:
            try:
                t0 = time.monotonic_ns()
                comp = gw.complete(ctx, msgs, cfg)
                lat_q = round((time.monotonic_ns() - t0) / 1_000_000.0, 2)
                cap_q = spy.captures[-1] if (spy and len(spy.captures) > cap_before) else {}
                in_tok = comp.usage.input_tokens or (cap_q.get("prompt_tokens") or 0)
                out_tok = comp.usage.output_tokens or (cap_q.get("completion_tokens") or 0)
                neur_q = estimate_cloudflare_neurons(model_id, input_tokens=in_tok, output_tokens=out_tok)
                report["requests"][key] = {
                    "finish_reason": cap_q.get("finish_reason"),
                    "truncated": cap_q.get("finish_reason") == "length",
                    "tokens": {
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "total_tokens": comp.usage.total_tokens or (cap_q.get("total_tokens") or (in_tok + out_tok)),
                    },
                    "latency_ms": cap_q.get("client_latency_ms") or lat_q,
                    "worker_latency_ms": cap_q.get("worker_latency_ms"),
                    "worker_request_id": cap_q.get("worker_request_id"),
                    "wire_bytes": cap_q.get("wire_bytes") or preflight_requests[key]["wire_bytes"],
                    "neurons": neur_q,
                    "response": comp.content,
                    "error": None,
                }
            except (AIError, httpx.HTTPError, RuntimeError, ValueError) as exc:
                cap_q = spy.captures[-1] if (spy and len(spy.captures) > cap_before) else {}
                in_tok = cap_q.get("prompt_tokens") or 0
                out_tok = cap_q.get("completion_tokens") or 0
                neur_q = estimate_cloudflare_neurons(model_id, input_tokens=in_tok, output_tokens=out_tok)
                report["requests"][key] = {
                    "finish_reason": cap_q.get("finish_reason"),
                    "truncated": cap_q.get("finish_reason") == "length",
                    "tokens": {
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "total_tokens": cap_q.get("total_tokens") or (in_tok + out_tok),
                    },
                    "latency_ms": cap_q.get("client_latency_ms"),
                    "worker_latency_ms": cap_q.get("worker_latency_ms"),
                    "worker_request_id": cap_q.get("worker_request_id"),
                    "wire_bytes": cap_q.get("wire_bytes") or preflight_requests[key]["wire_bytes"],
                    "neurons": neur_q,
                    "response": cap_q.get("raw_content"),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                stop_experiment = True
        else:
            pf = preflight_requests[key]
            _dry_comp = gw.complete(ctx, msgs, cfg)
            report["requests"][key] = {
                "finish_reason": None,
                "truncated": False,
                "tokens": {
                    "est_input_tokens_full_serialized_request": pf["estimated_input_tokens_full_serialized_request"],
                    "worst_case_output_tokens": 500,
                },
                "latency_ms": 0.0,
                "wire_bytes": pf["wire_bytes"],
                "neurons": pf["worst_case_neurons"],
                "response": _dry_comp.content,
                "error": None,
            }

    report["outbound_attempts_executed"] = prior_attempts + (spy.attempts if spy else 0)
    total_neurons = sum(
        float((v.get("neurons") or {}).get("estimated_neurons") or 0.0)
        for v in report["requests"].values()
        if isinstance(v, dict)
    )
    report["actual_total_neurons"] = round(total_neurons, 4)
    report["pct_of_daily_free_tier"] = round((total_neurons / FREE_TIER_DAILY_NEURONS) * 100.0, 4)

    output_path = args.output or (DEFAULT_RESULTS_PATH if args.live_cloudflare else None)
    rendered = json.dumps(report, indent=2)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
