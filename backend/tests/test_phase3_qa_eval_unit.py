"""Unit tests for Batch 3.4B independent Q&A scoring, prompt isolation, and paraphrase check."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_eval_module():
    spec = importlib.util.spec_from_file_location(
        "evaluate_qa", REPO_ROOT / "scripts" / "evaluate_qa.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_qa_eval_harness_runs_24_questions_without_prompt_leakage_and_exposes_paraphrase_rejection() -> None:
    mod = _load_eval_module()
    report = mod.run_qa_evaluation()

    assert report["question_count"] == 24
    assert report["live_inference_executed"] is False
    assert report["held_out_reference_leakage_checked"] is True

    profiles = report["profiles"]

    # 1. Grounded verbatim baseline passes all 24 contract checks and abstains/clarifies on the 3 unanswerable ones
    verb = profiles["grounded_verbatim"]
    assert verb["contract_pass_count"] == 24
    assert verb["false_abstentions_on_answerable"] == 0
    assert verb["rejected_citations_total"] == 0
    assert verb["unanswerable_mean_total"] >= 9.0
    assert verb["wire_metrics"]["all_within_16kb_worker_ceiling"] is True

    # 2. Paraphrase and mathematical reasoning answers (high pre-filter facet coverage) are
    #    excessively rejected by qa.py's verbatim substring check.
    para = profiles["paraphrase_and_math"]
    analysis = para["paraphrase_and_math_rejection_analysis"]
    assert analysis["answerable_candidates_tested"] == 21
    assert analysis["mean_pre_filter_facet_coverage"] >= 0.9
    assert analysis["rejected_by_verbatim_validation_count"] == 21
    assert analysis["rejected_by_verbatim_validation_rate"] == 1.0
    assert para["false_abstentions_on_answerable"] == 21

    # 3. Adversarial fabricated citations & empty retrieval fail closed cleanly
    adv = profiles["adversarial_fabricated"]
    assert adv["contract_pass_count"] == 24
    assert adv["rejected_citations_total"] == 24
    assert adv["dropped_claims_total"] == 24

    empty = profiles["empty_retrieval"]
    assert empty["contract_pass_count"] == 24
    assert empty["inference_requests_total"] == 0

    # 4. Bounded budget checks fit within Cloudflare 16 KB worker ceiling and 10k free daily Neurons
    budget = report["proposed_bounded_eval_budget"]
    assert budget["max_requests_per_eval_run"] == 24
    assert budget["max_inference_requests_per_question"] == 1
    assert budget["all_24_within_16kb_worker_ceiling"] is True
    assert budget["cloudflare_models"]["@cf/meta/llama-3.1-8b-instruct-fast"]["worst_case_24q_neurons"] < 1000
