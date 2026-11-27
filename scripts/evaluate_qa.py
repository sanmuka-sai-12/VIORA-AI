"""Reproducible zero-inference academic Q&A evaluation harness over the 24 DSA questions.

Measures independent rubric scores (factual correctness, conceptual completeness,
citation support, abstention, clarification), grounding/contract invariants,
excessive rejection of valid paraphrases/mathematical reasoning, latency, token
usage, and bounded live-evaluation budgets.

Run (zero network, deterministic fake gateway):
  python scripts/evaluate_qa.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from viora.ai.public import (
    CLOUDFLARE_MODEL_SHORTLIST,
    DEFAULT_CLOUDFLARE_MODEL,
    FREE_TIER_DAILY_NEURONS,
    AIRequestConfig,
    ChatMessage,
    FakeAIGateway,
    estimate_cloudflare_neurons,
)
from viora.contracts import TenantContext
from viora.knowledge.public import (
    ChunkingConfig,
    GraphEnrichmentConcept,
    GroundedKnowledgeQA,
    KnowledgeQARequest,
    KnowledgeQAResult,
    KnowledgeRetrievalResult,
    RawQAAnswer,
    RawQACitation,
    RawQAClaim,
    RetrievedEvidenceChunk,
    chunk_extracted_document,
    estimate_tokens,
)
from viora.sources.public import (
    AdaptiveDocumentExtractor,
    clamp_source_limits,
)

DSA_DIR = REPO_ROOT / "benchmarks" / "dsa_c_corpus"
EVAL_QUESTIONS_PATH = DSA_DIR / "eval_questions.json"
CORPUS_CONCEPTS_PATH = DSA_DIR / "corpus_concepts.json"
WORKER_MAX_BODY_BYTES = 16 * 1024  # cloudflare/viora-worker/index.js ceiling

_NORM_RE = re.compile(r"[^a-z0-9_+*/^()\[\]=<>.-]+")


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * (pct / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _compact_raw_qa_schema() -> dict[str, Any]:
    def _strip(node: Any, *, in_props: bool = False) -> Any:
        if isinstance(node, dict):
            return {
                k: _strip(v, in_props=(k == "properties"))
                for k, v in node.items()
                if in_props or k != "title"
            }
        if isinstance(node, list):
            return [_strip(x) for x in node]
        return node

    return _strip(RawQAAnswer.model_json_schema())


_COMPACT_QA_SCHEMA = _compact_raw_qa_schema()


# Held-out deterministic evaluation facets per question (NEVER placed in prompts).
# Each tuple entry lists alternative signal groups; a criterion is satisfied if any
# token/phrase in each required group appears in the evaluated response text.
_HELD_OUT_FACETS: dict[str, tuple[tuple[str, ...], ...]] = {
    "eval-def-01": (
        ("sizeof(*ptr)", "element size", "pointed type", "sizeof(*arr)"),
        ("decay", "int *", "pointer size", "sizeof(a)"),
    ),
    "eval-def-02": (
        ("sentinel", "non-null", "boundary", "prev", "next"),
        ("count", "head == tail", "empty", "full"),
    ),
    "eval-def-03": (
        ("empty", "terminat", "stops", "ht_empty"),
        ("tombstone", "ht_tombstone", "reuse", "duplicate", "probing", "probe"),
    ),
    "eval-reas-01": (
        ("realloc", "relocate", "new heap", "free"),
        ("dangling", "invalidat", "undefined behavior"),
    ),
    "eval-reas-02": (
        ("enqueue", "dequeue", "duplicate-pop", "pop time"),
        ("terminat", "visited"),
        ("duplicate", "exponential", "repeated"),
        ("occupancy", "insertions", "circular", "linear"),
        ("o(v + e)", "o(v^2)", "adjacency"),
        ("capacity", "out-of-bounds", "undefined behavior"),
    ),
    "eval-reas-03": (
        ("use-after-free", "node->left", "node->right", "child"),
        ("postorder", "subtrees first", "parent last"),
    ),
    "eval-comp-01": (
        ("theta(n^2)", "o(n^2)", "arithmetic", "32 + 64"),
        ("amortized", "theta(n)", "o(n)"),
    ),
    "eval-comp-02": (
        ("theta(n log n)", "o(n)", "merge sort", "scratch"),
        ("theta(n^2)", "o(n^2)", "skewed", "o(n) stack", "height n"),
    ),
    "eval-comp-03": (
        ("o(v^2)", "theta(v^2)", "matrix", "10^8"),
        ("o(v + e)", "theta(v + e)", "adjacency list"),
    ),
    "eval-impl-01": (
        ("nxt = cur->next", "save", "next"),
        ("**head", "*head = prev", "pointer-to-pointer"),
        ("o(1)", "extra space", "without allocating"),
    ),
    "eval-impl-02": (
        ("c + 1 < size", "right-child", "2*i + 1", "smaller child"),
        ("swap", "sift", "items[i] <= items[c]"),
    ),
    "eval-impl-03": (
        ("<=", "stable", "stability"),
        ("drain", "remaining", "copy", "tmp"),
    ),
    "eval-dbg-01": (
        ("probe", "truncat", "breaks", "strand", "ht_empty"),
        ("ht_tombstone", "tombstone"),
    ),
    "eval-dbg-02": (
        ("leak", "null", "destroys", "original"),
        ("tmp", "temporary"),
    ),
    "eval-dbg-03": (
        ("n == 0", "mid == 0", "size_max", "underflow", "wrap"),
        ("[lo, hi)", "half-open"),
    ),
    "eval-sel-01": (
        ("doubly", "circular"),
        ("o(1)", "unlink", "handle", "shifting"),
    ),
    "eval-sel-02": (
        ("heap", "min-heap", "max-heap", "priority"),
        ("k << n", "avoid", "o(n log n)", "sorting all"),
    ),
    "eval-sel-03": (
        ("adjacency list", "bfs", "queue"),
        ("o(v^2)", "matrix", "10^10", "o(v + e)"),
    ),
    "eval-edge-01": (
        ("hi - lo <= 1", "lo < hi", "base", "empty", "single"),
        ("n == 0", "-1", "without touching", "zero memory"),
    ),
    "eval-edge-02": (
        ("in place", "existing key", "update"),
        ("tombstone", "full", "-1", "earliest"),
    ),
    "eval-edge-03": (
        ("padding", "indeterminate", "holes"),
        ("fieldwise", "memset", "zero", "calloc"),
    ),
    "eval-ambig-01": (
        ("hardware", "compiler", "millisecond", "benchmark"),
        ("key", "order", "sorted", "random", "distribution"),
    ),
    "eval-ambig-02": (
        ("distinct", "different", "reordering", "lookup", "membership"),
        ("repeated", "ratio", "one-off", "amortiz"),
    ),
    "eval-ambig-03": (
        ("outside", "not in", "does not", "corpus", "fibonacci"),
        ("binary min-heap", "unweighted bfs", "missing", "insufficient"),
    ),
}


def _response_searchable_text(res: KnowledgeQAResult) -> str:
    parts = [
        res.answer,
        *(c.statement for c in res.source_grounded_claims),
    ]
    if res.clarification is not None:
        parts.append(res.clarification.reason)
        parts.extend(res.clarification.ambiguities)
        parts.extend(res.clarification.clarifying_questions)
    if res.abstention is not None:
        parts.append(res.abstention.reason)
        parts.extend(res.abstention.missing_evidence_topics)
    return " ".join(parts).lower()


def _facet_hits(text: str, question_id: str) -> tuple[int, int]:
    groups = _HELD_OUT_FACETS.get(question_id, ())
    if not groups:
        return 0, 0
    hits = sum(1 for group in groups if any(term in text for term in group))
    return hits, len(groups)


def score_qa_result(
    question_spec: dict[str, Any],
    result: KnowledgeQAResult,
    *,
    expected_source_ids: set[UUID] | None = None,
) -> dict[str, Any]:
    """Independent deterministic 5-dimension rubric (0-2 each, max 10) kept outside prompts."""
    qid = str(question_spec["question_id"])
    answerable = bool(question_spec["answerable"])
    text_lower = _response_searchable_text(result)
    hits, total_facets = _facet_hits(text_lower, qid)
    coverage = (hits / total_facets) if total_facets else 0.0

    # 1. Factual correctness (0-2)
    if answerable:
        if result.status == "answered" and coverage >= 0.75:
            factual = 2
        elif result.status == "answered" and coverage > 0.0:
            factual = 1
        elif result.status == "answered":
            factual = 1 if result.source_grounded_claims else 0
        else:
            factual = 0
    else:
        # Unanswerable/ambiguous: penalize if fabricated as a definitive grounded answer
        if result.status == "answered":
            factual = 0
        elif coverage >= 0.5:
            factual = 2
        else:
            factual = 1

    # 2. Conceptual completeness (0-2)
    if answerable:
        if result.status == "answered" and coverage >= 0.75:
            completeness = 2
        elif result.status == "answered" and coverage > 0.0:
            completeness = 1
        else:
            completeness = 0
    else:
        if result.status != "answered" and coverage >= 0.75:
            completeness = 2
        elif result.status != "answered" and coverage > 0.0:
            completeness = 1
        else:
            completeness = 0

    # 3. Citation support (0-2)
    cited_sources = {c.source_id for c in result.citations}
    relevant_ok = (
        True
        if not expected_source_ids or not cited_sources
        else bool(cited_sources & expected_source_ids)
    )
    if answerable:
        if (
            result.status == "answered"
            and len(result.citations) >= 1
            and result.validation.rejected_citations_count == 0
            and result.validation.dropped_ungrounded_claims_count == 0
            and relevant_ok
        ):
            citation_support = 2
        elif len(result.citations) >= 1 and relevant_ok:
            citation_support = 1
        else:
            citation_support = 0
    else:
        if result.status != "answered" and result.validation.rejected_citations_count == 0 and not result.citations:
            citation_support = 2
        elif result.status != "answered" and result.validation.rejected_citations_count == 0:
            citation_support = 1
        else:
            citation_support = 0

    # 4. Abstention (0-2)
    false_abstention = answerable and result.status == "insufficient_evidence"
    if qid in {"eval-ambig-01", "eval-ambig-03"}:
        if (
            result.status == "insufficient_evidence"
            and result.abstention is not None
            and not result.source_grounded_claims
        ):
            abstention = 2
        elif result.status != "answered" and not result.source_grounded_claims:
            abstention = 1
        else:
            abstention = 0
    elif answerable:
        abstention = 2 if result.status == "answered" else 0
    else:
        # eval-ambig-02: clarification_needed or insufficient_evidence without hallucinating
        abstention = 2 if result.status != "answered" and not result.source_grounded_claims else 0

    # 5. Clarification (0-2)
    if qid == "eval-ambig-02":
        if (
            result.status == "clarification_needed"
            and result.clarification is not None
            and len(result.clarification.clarifying_questions) >= 1
        ):
            clarification = 2
        elif result.clarification is not None or "?" in result.answer:
            clarification = 1
        else:
            clarification = 0
    elif qid == "eval-ambig-01":
        if result.clarification is not None and len(result.clarification.clarifying_questions) >= 1:
            clarification = 2
        elif result.status == "insufficient_evidence":
            clarification = 1
        else:
            clarification = 0
    else:
        clarification = 0 if result.status == "clarification_needed" else 2

    total = factual + completeness + citation_support + abstention + clarification
    contract_passed = (
        result.validation.inference_requests <= 1
        and result.general_knowledge is None
        and (
            (result.status == "answered" and len(result.source_grounded_claims) >= 1 and len(result.citations) >= 1)
            or (result.status == "clarification_needed" and result.clarification is not None)
            or (result.status == "insufficient_evidence" and result.abstention is not None)
        )
    )
    return {
        "question_id": qid,
        "category": question_spec["category"],
        "answerable": answerable,
        "status": result.status,
        "contract_passed": contract_passed,
        "false_abstention": false_abstention,
        "facet_hits": hits,
        "facet_total": total_facets,
        "scores": {
            "factual_correctness": factual,
            "conceptual_completeness": completeness,
            "citation_support": citation_support,
            "abstention": abstention,
            "clarification": clarification,
            "total": total,
            "max_total": 10,
        },
        "grounding": {
            "claims": len(result.source_grounded_claims),
            "valid_citations": result.validation.valid_citations_count,
            "rejected_citations": result.validation.rejected_citations_count,
            "dropped_claims": result.validation.dropped_ungrounded_claims_count,
        },
    }


def _build_corpus_fixtures(
    tenant_id: UUID,
) -> tuple[dict[str, tuple[RetrievedEvidenceChunk, ...]], dict[str, GraphEnrichmentConcept], dict[str, UUID]]:
    """Build real structure-aware chunks & evidence refs from corpus_concepts.json."""
    concepts = json.loads(CORPUS_CONCEPTS_PATH.read_text(encoding="utf-8"))["concepts"]
    extractor = AdaptiveDocumentExtractor()
    limits = clamp_source_limits()
    chunks_by_cid: dict[str, tuple[RetrievedEvidenceChunk, ...]] = {}
    graph_by_cid: dict[str, GraphEnrichmentConcept] = {}
    source_by_cid: dict[str, UUID] = {}

    for c in concepts:
        cid = str(c["concept_id"])
        sid, vid, ext_id, ce_id = uuid4(), uuid4(), uuid4(), uuid4()
        source_by_cid[cid] = sid
        body = (
            f"# {c['title']}\n\n"
            f"{c['definition']}\n\n"
            f"{c['working_principle']}\n\n"
            f"Complexity: worst={c['time_complexity']['worst']}; aux_space={c['auxiliary_space_complexity']}.\n\n"
            f"Preconditions: {c['preconditions_and_limitations']}\n\n"
            f"Common mistakes: {'; '.join(c['common_mistakes'])}\n\n"
            f"C example: `{c['c_example']}`"
        )
        raw_bytes = body.encode("utf-8")
        sha = hashlib.sha256(raw_bytes).hexdigest()
        doc = extractor.extract(
            tenant_id=tenant_id,
            source_id=sid,
            version_id=vid,
            source_version=1,
            source_sha256=sha,
            input_format="typed_notes",
            title=str(c["title"]),
            raw_bytes=raw_bytes,
            limits=limits,
        )
        plan = chunk_extracted_document(
            doc, config=ChunkingConfig(single_pass_max_tokens=1600, chunk_max_tokens=900)
        )
        built_chunks: list[RetrievedEvidenceChunk] = []
        for ch in plan.chunks:
            built_chunks.append(
                RetrievedEvidenceChunk(
                    chunk_record_id=uuid4(),
                    source_id=sid,
                    version_id=vid,
                    derived_extraction_id=ext_id,
                    concept_extraction_id=ce_id,
                    index_version=1,
                    chunk_id=ch.chunk_id,
                    chunk_index=ch.chunk_index,
                    section_id=ch.section_id,
                    section_path=ch.section_path,
                    heading_title=ch.heading_title,
                    page_start=ch.page_start,
                    page_end=ch.page_end,
                    evidence_ids=ch.evidence_ids,
                    origin_kind="teacher_resource",
                    visibility="offering_published",
                    rendered_text=ch.rendered_text,
                    parent_context=f"Section [{c['topic']}]",
                    estimated_tokens=ch.estimated_tokens,
                )
            )
        chunks_by_cid[cid] = tuple(built_chunks)
        graph_by_cid[cid] = GraphEnrichmentConcept(
            concept_id=cid,
            name=str(c["title"]),
            grounding="source_supported",
            canonical_summary=str(c["definition"]),
            evidence_ids=built_chunks[0].evidence_ids[:2] if built_chunks else (),
            hop_distance=0,
        )
    return chunks_by_cid, graph_by_cid, source_by_cid


# Paraphrased / mathematically reasoned candidate answers (valid educational explanations
# synthesized from the corpus concepts without copying sentences verbatim).
_PARAPHRASE_AND_MATH_CLAIMS: dict[str, tuple[str, str]] = {
    "eval-def-01": (
        "Incrementing a struct Node * advances by sizeof(struct Node) bytes due to pointer scaling, while int a[] decays to int * inside functions so sizeof(a) yields pointer size.",
        "source_synthesized",
    ),
    "eval-def-02": (
        "A circular sentinel guarantees non-NULL prev and next links on every node, whereas an explicit count distinguishes full (count == CAP) from empty (count == 0) in a ring buffer.",
        "source_synthesized",
    ),
    "eval-def-03": (
        "An empty slot ends open-address probing immediately, while a tombstone lets searches continue probing and can be reclaimed once duplicate keys ahead are ruled out.",
        "source_supported",
    ),
    "eval-reas-01": (
        "Appending at capacity triggers realloc, which can move the buffer to a new heap address and free the old memory, leaving &dyn.data[2] dangling.",
        "source_supported",
    ),
    "eval-reas-02": (
        "Dequeue-time marking allows duplicate vertex insertions and potential exponential blowup without a duplicate-pop guard, and on a fixed int q[V] queue it overflows capacity (undefined behavior in C); enqueue-time marking preserves O(V + E) list and O(V^2) matrix bounds.",
        "source_supported",
    ),
    "eval-reas-03": (
        "Freeing a BST node in preorder destroys root->left and root->right before reading them (use-after-free), so children must be freed first in postorder.",
        "source_supported",
    ),
    "eval-comp-01": (
        "Growing by +32 copies 32 + 64 + ... + N = Theta(N^2 / 32) = Theta(N^2) elements total, degrading amortized append cost from O(1) to Theta(N).",
        "source_synthesized",
    ),
    "eval-comp-02": (
        "Top-down merge sort guarantees Theta(N log N) time with O(N) scratch space and O(log N) stack depth, while inserting sorted keys into an unbalanced BST degrades to Theta(N^2) time and O(N) stack depth.",
        "source_synthesized",
    ),
    "eval-comp-03": (
        "For V=10,000 and E=18,000, an adjacency matrix requires Theta(V^2) (~10^8) cells and O(V^2) BFS time, whereas adjacency lists use Theta(V + E) space and O(V + E) time.",
        "source_synthesized",
    ),
    "eval-impl-01": (
        "Save nxt = cur->next before setting cur->next = prev, advance prev and cur, and finish with *head = prev for O(1) extra space reversal.",
        "source_supported",
    ),
    "eval-impl-02": (
        "Move items[--size] into items[0], guard c + 1 < size when picking the smaller child, and swap downward while items[i] > items[c].",
        "source_supported",
    ),
    "eval-impl-03": (
        "Merge [lo, mid) and [mid, hi) into tmp using arr[i] <= arr[j] to preserve stability, drain remaining elements, and copy tmp back.",
        "source_supported",
    ),
    "eval-dbg-01": (
        "Writing HT_EMPTY on deletion truncates linear-probing chains for later keys; mark deleted slots as HT_TOMBSTONE instead.",
        "source_supported",
    ),
    "eval-dbg-02": (
        "Assigning realloc directly to arr->data loses and leaks the original buffer on NULL failure; assign into a temporary pointer tmp first.",
        "source_supported",
    ),
    "eval-dbg-03": (
        "Unsigned size_t underflows to SIZE_MAX when n == 0 in n - 1 or when mid == 0 in mid - 1; prefer half-open [lo, hi) bounds.",
        "source_supported",
    ),
    "eval-sel-01": (
        "A circular doubly linked list with sentinel supports O(1) unlinking from a node handle and O(1) endpoint updates without shifting or resizing.",
        "source_supported",
    ),
    "eval-sel-02": (
        "A binary heap selection structure avoids a full O(n log n) merge sort when only the k << n smallest elements are needed without stability.",
        "source_synthesized",
    ),
    "eval-sel-03": (
        "Pair adjacency lists with BFS backed by a circular ring queue for O(V + E) time and space, avoiding an O(V^2) matrix.",
        "source_synthesized",
    ),
    "eval-edge-01": (
        "Base guards hi - lo <= 1 for merge sort and lo < hi for binary search exit immediately on n == 0 without dereferencing memory, returning -1 for binary search.",
        "source_synthesized",
    ),
    "eval-edge-02": (
        "An existing key updates in place across the probe cycle, while a new key fills the earliest HT_TOMBSTONE or returns -1 if none exists.",
        "source_supported",
    ),
    "eval-edge-03": (
        "Member-wise struct initialization leaves alignment padding holes indeterminate, so bytewise memcmp fails; compare fields individually or zero-init with memset/calloc.",
        "source_supported",
    ),
}


class _PromptLeakGuardedGateway(FakeAIGateway):
    """FakeAIGateway that enforces zero held-out reference leakage & captures wire metrics."""

    def __init__(
        self,
        *,
        profile: str,
        questions_by_prompt: dict[str, dict[str, Any]],
        chunks_by_cid: dict[str, tuple[RetrievedEvidenceChunk, ...]],
        held_out_secrets: list[tuple[str, str]],
    ) -> None:
        super().__init__(model_name=f"eval-fake-{profile}")
        self.profile = profile
        self.questions_by_prompt = questions_by_prompt
        self.chunks_by_cid = chunks_by_cid
        self.held_out_secrets = held_out_secrets
        self.wire_metrics: list[dict[str, Any]] = []

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model,
        config: AIRequestConfig | None = None,
    ):
        joined_prompt = "\n".join(m.content for m in messages)
        for qid, secret in self.held_out_secrets:
            if len(secret) >= 24 and secret in joined_prompt:
                raise AssertionError(
                    f"Held-out reference material from {qid} leaked into model prompt!"
                )

        cfg = config or AIRequestConfig()
        body = {
            "model": DEFAULT_CLOUDFLARE_MODEL,
            "messages": [{"role": m.role.value, "content": m.content} for m in messages],
            "response_format": {"type": "json_schema", "json_schema": _COMPACT_QA_SCHEMA},
        }
        if cfg.max_tokens is not None:
            body["max_tokens"] = cfg.max_tokens
        if cfg.temperature is not None:
            body["temperature"] = cfg.temperature
        wire_json = json.dumps(body, ensure_ascii=False)
        wire_bytes = len(wire_json.encode("utf-8"))
        self.wire_metrics.append(
            {
                "wire_bytes": wire_bytes,
                "within_16kb_worker_ceiling": wire_bytes < WORKER_MAX_BODY_BYTES,
                "est_input_tokens_serialized": estimate_tokens(wire_json),
                "est_input_tokens_messages": estimate_tokens(joined_prompt),
            }
        )

        user_txt = messages[-1].content
        q_start = user_txt.find("<untrusted_student_question>\n") + len(
            "<untrusted_student_question>\n"
        )
        q_end = user_txt.find("\n</untrusted_student_question>")
        prompt_q = user_txt[q_start:q_end].strip()
        q_info = self.questions_by_prompt[prompt_q]
        qid = str(q_info["question_id"])

        payload = self._build_payload(qid=qid, q_info=q_info, user_txt=user_txt)
        self.structured_payloads[response_model] = payload
        return super().complete_structured(context, messages, response_model, config)

    def _build_payload(
        self,
        *,
        qid: str,
        q_info: dict[str, Any],
        user_txt: str,
    ) -> RawQAAnswer:
        if self.profile == "adversarial_fabricated":
            return RawQAAnswer(
                status="answered",
                answer="Hallucinated answer citing fabricated evidence.",
                claims=[
                    RawQAClaim(
                        statement="Fabricated claim.",
                        citations=[RawQACitation(chunk_ref="C99", evidence_id="ev-fake")],
                    )
                ],
                general_knowledge_answer="Unrequested general knowledge.",
            )

        if qid == "eval-ambig-01":
            return RawQAAnswer(
                status="insufficient_evidence",
                answer="Cannot compute exact runtime in milliseconds without hardware, compiler flags, and key order (sorted O(n^2) vs random O(n log n)).",
                abstention_reason="Wall-clock millisecond runtime requires target hardware/compiler benchmark context and key distribution.",
                missing_evidence_topics=["Hardware/compiler environment", "Key insertion order"],
                clarifying_questions=["Are the 1,000,000 keys sorted or random, and what hardware/compiler flags apply?"],
            )
        if qid == "eval-ambig-02":
            return RawQAAnswer(
                status="clarification_needed",
                answer="Sorting (reordering) and searching (membership lookup) solve distinct tasks; the choice depends on query-to-update ratio.",
                ambiguities=["Distinct problem classes: sorting reorders vs search checks membership."],
                clarifying_questions=["Do you perform repeated lookups (amortizing O(n log n) sort + O(log n) search) or a one-off lookup?"],
            )
        if qid == "eval-ambig-03":
            return RawQAAnswer(
                status="insufficient_evidence",
                answer="Outside the provided corpus: the corpus covers binary min-heaps and unweighted BFS, not Fibonacci heap Dijkstra bounds.",
                abstention_reason="Fibonacci heap amortized decrease-key bounds are missing from the provided corpus.",
                missing_evidence_topics=["Fibonacci heap Dijkstra complexity"],
            )

        ev_id = "ev-1"
        ref_idx = user_txt.find("[REF: C1")
        if ref_idx != -1:
            hdr = user_txt[ref_idx : user_txt.find("]", ref_idx)]
            if "ev:" in hdr:
                ev_id = hdr.split("ev:", 1)[1].split(",", 1)[0].strip() or "ev-1"

        if self.profile == "grounded_verbatim":
            first_cid = str(q_info["relevant_concept_ids"][0])
            c_chunk = self.chunks_by_cid[first_cid][0]
            first_line = next(
                (
                    ln.strip()
                    for ln in c_chunk.rendered_text.splitlines()
                    if ln.strip() and not ln.strip().startswith("#")
                ),
                c_chunk.rendered_text[:200],
            )
            return RawQAAnswer(
                status="answered",
                answer=first_line,
                claims=[
                    RawQAClaim(
                        statement=first_line,
                        grounding="source_supported",
                        citations=[RawQACitation(chunk_ref="C1", evidence_id=ev_id)],
                    )
                ],
            )

        if self.profile == "paraphrase_and_math":
            stmt, grounding = _PARAPHRASE_AND_MATH_CLAIMS[qid]
            cits = [RawQACitation(chunk_ref="C1", evidence_id=ev_id)]
            if "[REF: C2" in user_txt:
                cits.append(RawQACitation(chunk_ref="C2", evidence_id=ev_id))
            return RawQAAnswer(
                status="answered",
                answer=stmt,
                claims=[
                    RawQAClaim(
                        statement=stmt,
                        grounding=grounding,  # type: ignore[arg-type]
                        citations=cits,
                    )
                ],
            )

        raise ValueError(f"Unknown profile: {self.profile}")


def _aggregate_profile_report(
    *,
    profile: str,
    questions: list[dict[str, Any]],
    chunks_by_cid: dict[str, tuple[RetrievedEvidenceChunk, ...]],
    graph_by_cid: dict[str, GraphEnrichmentConcept],
    source_by_cid: dict[str, UUID],
    request_config: AIRequestConfig,
) -> dict[str, Any]:
    questions_by_prompt = {str(q["prompt"]): q for q in questions}
    held_out_secrets = [
        (str(q["question_id"]), str(q["expected_answer"])) for q in questions
    ] + [
        (str(q["question_id"]), crit)
        for q in questions
        for crit in q["evaluation_criteria"]
    ]
    ctx = TenantContext(actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"student"}))
    gw = _PromptLeakGuardedGateway(
        profile=profile,
        questions_by_prompt=questions_by_prompt,
        chunks_by_cid=chunks_by_cid,
        held_out_secrets=held_out_secrets,
    )
    qa = GroundedKnowledgeQA(gw, request_config=request_config)

    per_question: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    context_tokens_list: list[int] = []
    total_inference_requests = 0
    paraphrase_pre_filter_scores: list[dict[str, Any]] = []

    for q in questions:
        qid = str(q["question_id"])
        rel_ids = [str(cid) for cid in q["relevant_concept_ids"]]
        if profile == "empty_retrieval":
            q_chunks: tuple[RetrievedEvidenceChunk, ...] = ()
            q_graph: tuple[GraphEnrichmentConcept, ...] = ()
            expected_sources: set[UUID] = set()
        else:
            q_chunks = tuple(ch for cid in rel_ids for ch in chunks_by_cid[cid])
            q_graph = tuple(graph_by_cid[cid] for cid in rel_ids)
            expected_sources = {source_by_cid[cid] for cid in rel_ids}

        retrieval = KnowledgeRetrievalResult(
            mode="hybrid_graph",
            query=str(q["prompt"]),
            embedding_model="fake-hash-embedder",
            embedding_model_version="fake:1.0",
            chunks=q_chunks,
            graph_concepts=q_graph,
            context_text="\n\n".join(c.rendered_text for c in q_chunks),
            context_tokens=sum(c.estimated_tokens for c in q_chunks),
            duplicate_chunks_removed=0,
            cycles_avoided=0,
            hops_traversed=0,
            lexical_candidates=len(q_chunks),
            semantic_candidates=len(q_chunks),
            retrieval_ms=0.0,
        )
        res = qa.answer(
            context=ctx,
            request=KnowledgeQARequest(question=str(q["prompt"]), max_context_tokens=1800),
            retrieval=retrieval,
        )
        scored = score_qa_result(q, res, expected_source_ids=expected_sources)
        per_question.append(scored)
        latencies_ms.append(res.validation.total_ms)
        context_tokens_list.append(res.validation.context_tokens)
        total_inference_requests += res.validation.inference_requests

        # For paraphrase_and_math on answerable questions, also score the candidate BEFORE
        # qa.py's verbatim filter so we can quantify valid answers rejected solely by verbatim matching.
        if profile == "paraphrase_and_math" and q["answerable"]:
            cand_text, _ = _PARAPHRASE_AND_MATH_CLAIMS[qid]
            hits, total_f = _facet_hits(cand_text.lower(), qid)
            paraphrase_pre_filter_scores.append(
                {
                    "question_id": qid,
                    "pre_filter_facet_coverage": round(hits / total_f, 3) if total_f else 1.0,
                    "post_filter_status": res.status,
                    "rejected_by_verbatim_check": res.status == "insufficient_evidence",
                }
            )

    lat_sorted = sorted(latencies_ms)
    answerable_rows = [r for r in per_question if r["answerable"]]
    unanswerable_rows = [r for r in per_question if not r["answerable"]]
    summary: dict[str, Any] = {
        "profile": profile,
        "questions_evaluated": len(per_question),
        "inference_requests_total": total_inference_requests,
        "contract_pass_count": sum(1 for r in per_question if r["contract_passed"]),
        "false_abstentions_on_answerable": sum(1 for r in answerable_rows if r["false_abstention"]),
        "rejected_citations_total": sum(r["grounding"]["rejected_citations"] for r in per_question),
        "dropped_claims_total": sum(r["grounding"]["dropped_claims"] for r in per_question),
        "mean_scores": {
            dim: round(statistics.mean(r["scores"][dim] for r in per_question), 3)
            for dim in (
                "factual_correctness",
                "conceptual_completeness",
                "citation_support",
                "abstention",
                "clarification",
                "total",
            )
        },
        "answerable_mean_total": round(statistics.mean(r["scores"]["total"] for r in answerable_rows), 3),
        "unanswerable_mean_total": round(statistics.mean(r["scores"]["total"] for r in unanswerable_rows), 3),
        "latency_ms": {
            "mean": round(statistics.mean(lat_sorted), 3),
            "p50": round(statistics.median(lat_sorted), 3),
            "p95": round(_percentile(lat_sorted, 95), 3),
            "max": round(max(lat_sorted), 3),
        },
        "context_tokens": {
            "mean": round(statistics.mean(context_tokens_list), 1),
            "max": max(context_tokens_list),
        },
        "wire_metrics": {
            "requests_measured": len(gw.wire_metrics),
            "max_wire_bytes": max((m["wire_bytes"] for m in gw.wire_metrics), default=0),
            "all_within_16kb_worker_ceiling": all(m["within_16kb_worker_ceiling"] for m in gw.wire_metrics),
            "mean_est_input_tokens_serialized": round(
                statistics.mean(m["est_input_tokens_serialized"] for m in gw.wire_metrics), 1
            )
            if gw.wire_metrics
            else 0.0,
            "max_est_input_tokens_serialized": max(
                (m["est_input_tokens_serialized"] for m in gw.wire_metrics), default=0
            ),
        },
        "per_question": per_question,
    }
    if paraphrase_pre_filter_scores:
        rejected_valid = sum(1 for x in paraphrase_pre_filter_scores if x["rejected_by_verbatim_check"])
        summary["paraphrase_and_math_rejection_analysis"] = {
            "answerable_candidates_tested": len(paraphrase_pre_filter_scores),
            "mean_pre_filter_facet_coverage": round(
                statistics.mean(x["pre_filter_facet_coverage"] for x in paraphrase_pre_filter_scores), 3
            ),
            "rejected_by_verbatim_validation_count": rejected_valid,
            "rejected_by_verbatim_validation_rate": round(
                rejected_valid / len(paraphrase_pre_filter_scores), 4
            ),
        }
    return summary


def run_qa_evaluation() -> dict[str, Any]:
    eval_doc = json.loads(EVAL_QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions = list(eval_doc["questions"])
    tenant_id = uuid4()
    chunks_by_cid, graph_by_cid, source_by_cid = _build_corpus_fixtures(tenant_id)
    cfg = AIRequestConfig(max_tokens=700, temperature=0.0, max_retries=0)

    profiles = {
        name: _aggregate_profile_report(
            profile=name,
            questions=questions,
            chunks_by_cid=chunks_by_cid,
            graph_by_cid=graph_by_cid,
            source_by_cid=source_by_cid,
            request_config=cfg,
        )
        for name in (
            "grounded_verbatim",
            "paraphrase_and_math",
            "adversarial_fabricated",
            "empty_retrieval",
        )
    }

    # Compute bounded live-evaluation budget projections using observed serialized tokens
    verb_wire = profiles["grounded_verbatim"]["wire_metrics"]
    mean_in = round(verb_wire["mean_est_input_tokens_serialized"])
    max_in = int(verb_wire["max_est_input_tokens_serialized"])
    est_out = 350
    max_out = cfg.max_tokens or 700
    budget_by_model: dict[str, Any] = {}
    for model_id in CLOUDFLARE_MODEL_SHORTLIST:
        mean_per_q = estimate_cloudflare_neurons(model_id, input_tokens=mean_in, output_tokens=est_out)
        worst_per_q = estimate_cloudflare_neurons(model_id, input_tokens=max_in, output_tokens=max_out)
        n24_expected = round(float(mean_per_q["estimated_neurons"] or 0.0) * len(questions), 2)
        n24_worst = round(float(worst_per_q["estimated_neurons"] or 0.0) * len(questions), 2)
        budget_by_model[model_id] = {
            "expected_24q_neurons": n24_expected,
            "expected_pct_daily_free_tier": round((n24_expected / FREE_TIER_DAILY_NEURONS) * 100.0, 2),
            "worst_case_24q_neurons": n24_worst,
            "worst_case_pct_daily_free_tier": round((n24_worst / FREE_TIER_DAILY_NEURONS) * 100.0, 2),
        }

    return {
        "dataset_id": eval_doc["dataset_id"],
        "question_count": len(questions),
        "live_inference_executed": False,
        "held_out_reference_leakage_checked": True,
        "profiles": {
            k: {key: val for key, val in v.items() if key != "per_question"}
            for k, v in profiles.items()
        },
        "proposed_bounded_eval_budget": {
            "max_requests_per_eval_run": len(questions),
            "max_inference_requests_per_question": 1,
            "max_context_tokens": 1800,
            "max_output_tokens": max_out,
            "max_retries": 0,
            "worker_ceiling_bytes": WORKER_MAX_BODY_BYTES,
            "observed_max_wire_bytes": verb_wire["max_wire_bytes"],
            "all_24_within_16kb_worker_ceiling": verb_wire["all_within_16kb_worker_ceiling"],
            "mean_serialized_input_tokens": mean_in,
            "max_serialized_input_tokens": max_in,
            "daily_free_tier_neurons": FREE_TIER_DAILY_NEURONS,
            "cloudflare_models": budget_by_model,
        },
        "per_question_verbatim": profiles["grounded_verbatim"]["per_question"],
        "per_question_paraphrase_and_math": profiles["paraphrase_and_math"]["per_question"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-inference 24-DSA Q&A quality evaluation harness")
    parser.add_argument("--output", type=Path, default=None, help="Optional path to write JSON evaluation report")
    args = parser.parse_args(argv)
    report = run_qa_evaluation()
    rendered = json.dumps(report, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
