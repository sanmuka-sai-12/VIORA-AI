"""Compact JSON generation, validation, and server-side grading for flashcards & quizzes."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from ..security import AccessError
from .mind_map import _clean_text, loads_llm_json
from .schemas import (
    FlashcardDeck,
    FlashcardItem,
    PracticeExamActiveAttempt,
    PracticeExamAttemptRecord,
    PracticeExamData,
    PracticeExamQuestion,
    PracticeExamQuestionResult,
    PracticeExamTopicPerformance,
    PracticeQuizAttemptRecord,
    PracticeQuizData,
    PracticeQuizQuestionResult,
    QuizDifficulty,
    RawFlashcardsPayload,
    RawPracticeQuizPayload,
    StudentSafeQuizQuestion,
)


def compute_study_item_counts(context_chars: int) -> tuple[int, int]:
    """Choose token-aware (flashcard_count, quiz_question_count) for compact 480-token output."""
    if context_chars >= 1_800:
        return (4, 3)
    return (5, 4)


def build_flashcards_system_prompt(card_count: int = 5) -> str:
    return (
        "You are VIORA, an academic tutor generating concise study flashcards as compact JSON.\n"
        "Return ONLY valid JSON (no markdown fences, no commentary) matching this exact shape:\n"
        '{"title":"Topic","cards":[{"front":"Concise question or term","back":"Clear 1-sentence answer","hint":"Short cue or null"}]}\n'
        "RULES:\n"
        f"- Produce {card_count} cards so the entire JSON fits comfortably within 360 tokens.\n"
        "- Keep front <= 90 chars, back <= 140 chars, hint <= 60 chars (or null).\n"
        "- Prefer clean Unicode math (μ, σ, →, ∞, Σ) or double-escaped LaTeX (\\\\frac, \\\\mu) in JSON strings.\n"
        "- NO executable code unless the topic is programming. Ground cards in the provided study context."
    )


FLASHCARDS_SYSTEM_PROMPT = build_flashcards_system_prompt(5)


def build_practice_quiz_system_prompt(
    difficulty: QuizDifficulty,
    *,
    question_count: int = 4,
) -> str:
    level_guidance = {
        "easy": "Focus on foundational definitions, direct recall, and straightforward single-step checks.",
        "medium": "Focus on conceptual understanding, relationships, and short applied problems.",
        "hard": "Focus on multi-step reasoning, subtle distinctions, edge cases, and synthesis.",
    }[difficulty]
    return (
        f"You are VIORA, an academic tutor creating a {difficulty}-level multiple-choice practice quiz as compact JSON.\n"
        "Return ONLY valid JSON (no markdown fences, no commentary) matching this exact shape:\n"
        '{"title":"Topic Quiz","questions":[{"prompt":"Question text?","options":["Option A","Option B","Option C","Option D"],"correct_index":0,"explanation":"Brief 1-sentence reason."}]}\n'
        "RULES:\n"
        f"- Difficulty: {difficulty}. {level_guidance}\n"
        f"- Produce {question_count} questions, each with EXACTLY 4 distinct options and integer correct_index (0, 1, 2, or 3).\n"
        "- Keep prompt <= 120 chars, each option <= 55 chars, explanation <= 110 chars so JSON fits within 400 tokens.\n"
        "- Prefer clean Unicode math (μ, σ, →, ∞, Σ) or double-escaped LaTeX (\\\\frac, \\\\mu) in JSON strings.\n"
        "- NO executable code unless the topic is programming. Ground questions in the provided study context."
    )


def parse_flashcards_response(
    raw_content: str,
    *,
    finish_reason: str | None = None,
) -> FlashcardDeck:
    if finish_reason == "length":
        raise AccessError(
            422,
            "flashcards_incomplete",
            "Flashcards output reached the response length limit before finishing. Try narrowing the focus topic or clicking Regenerate.",
        )

    try:
        parsed_obj = loads_llm_json(raw_content)
        raw_payload = RawFlashcardsPayload.model_validate(parsed_obj)
    except (json.JSONDecodeError, ValidationError):
        raise AccessError(
            422,
            "flashcards_incomplete",
            "Flashcards output was incomplete or malformed. Try narrowing the focus topic or clicking Regenerate.",
        ) from None

    cards: list[FlashcardItem] = []
    for idx, item in enumerate(raw_payload.cards[:8], start=1):
        front = _clean_text(item.front, max_len=160)
        back = _clean_text(item.back, max_len=260)
        if not front or not back:
            continue
        hint = _clean_text(item.hint, max_len=120) or None
        cards.append(
            FlashcardItem(
                card_id=f"c{idx}",
                front=front,
                back=back,
                hint=hint,
                status="unseen",
            )
        )

    if len(cards) < 2:
        raise AccessError(
            422,
            "flashcards_incomplete",
            "Could not build enough valid flashcards from this output. Please try regenerating.",
        )

    title = _clean_text(raw_payload.title, max_len=120) or "Study Flashcards"
    return FlashcardDeck(title=title, cards=tuple(cards))


def flashcards_to_markdown(deck: FlashcardDeck) -> str:
    lines: list[str] = []
    for idx, card in enumerate(deck.cards, start=1):
        lines.append(f"{idx}. **{card.front}**\n   - Answer: {card.back}")
    return "\n".join(lines)


def parse_practice_quiz_response(
    raw_content: str,
    *,
    difficulty: QuizDifficulty,
    finish_reason: str | None = None,
) -> dict[str, Any]:
    if finish_reason == "length":
        raise AccessError(
            422,
            "quiz_incomplete",
            "Practice quiz output reached the response length limit before finishing. Try narrowing the focus topic or clicking Regenerate.",
        )

    try:
        parsed_obj = loads_llm_json(raw_content)
        raw_payload = RawPracticeQuizPayload.model_validate(parsed_obj)
    except (json.JSONDecodeError, ValidationError):
        raise AccessError(
            422,
            "quiz_incomplete",
            "Practice quiz output was incomplete or malformed. Try narrowing the focus topic or clicking Regenerate.",
        ) from None

    questions: list[dict[str, Any]] = []
    for idx, q in enumerate(raw_payload.questions[:6], start=1):
        prompt = _clean_text(q.prompt, max_len=240)
        explanation = _clean_text(q.explanation, max_len=260)
        opts = [_clean_text(opt, max_len=140) for opt in q.options]
        topic_tag = _clean_text(q.topic or "", max_len=60) or "General"
        if not prompt or not explanation or len(opts) != 4 or any(not o for o in opts):
            continue
        questions.append(
            {
                "question_id": f"q{idx}",
                "prompt": prompt,
                "options": opts,
                "correct_index": int(q.correct_index),
                "explanation": explanation,
                "topic_tag": topic_tag,
            }
        )

    if len(questions) < 2:
        raise AccessError(
            422,
            "quiz_incomplete",
            "Could not build enough valid quiz questions from this output. Please try regenerating.",
        )

    title = _clean_text(raw_payload.title, max_len=120) or "Practice Quiz"
    return {
        "title": title,
        "difficulty": difficulty,
        "questions": questions,
        "attempts": [],
    }


def quiz_to_student_markdown(structured_data: dict[str, Any]) -> str:
    """Student-safe copy text (questions & choices only, no answer key leakage)."""
    lines: list[str] = []
    labels = ("A", "B", "C", "D")
    for idx, q in enumerate(structured_data.get("questions", ()), start=1):
        lines.append(f"{idx}. {q.get('prompt', '')}")
        for o_idx, opt in enumerate(q.get("options", ())):
            tag = labels[o_idx] if o_idx < len(labels) else str(o_idx + 1)
            lines.append(f"   {tag}) {opt}")
    return "\n".join(lines)


def project_student_safe_quiz(structured_data: dict[str, Any]) -> PracticeQuizData:
    """Return quiz questions stripped of correct_index/explanation until submission."""
    questions = tuple(
        StudentSafeQuizQuestion(
            question_id=str(q["question_id"]),
            prompt=str(q["prompt"]),
            options=tuple(str(opt) for opt in q["options"]),
        )
        for q in structured_data.get("questions", ())
    )
    attempts = tuple(
        PracticeQuizAttemptRecord(**att)
        for att in structured_data.get("attempts", ())
    )
    return PracticeQuizData(
        title=str(structured_data.get("title") or "Practice Quiz"),
        difficulty=structured_data.get("difficulty", "medium"),
        questions=questions,
        attempts=attempts,
    )


def score_practice_quiz(
    structured_data: dict[str, Any],
    answers: dict[str, int],
) -> tuple[PracticeQuizAttemptRecord, dict[str, Any]]:
    raw_questions = list(structured_data.get("questions") or ())
    if not raw_questions:
        raise AccessError(422, "invalid_quiz", "Practice quiz contains no questions")

    results: list[PracticeQuizQuestionResult] = []
    correct_count = 0

    for q in raw_questions:
        qid = str(q["question_id"])
        if qid not in answers:
            raise AccessError(
                422,
                "incomplete_quiz_submission",
                "Please select an answer for every question before submitting.",
            )
        selected = int(answers[qid])
        options = tuple(str(opt) for opt in q["options"])
        if selected < 0 or selected >= len(options):
            raise AccessError(
                422,
                "invalid_quiz_answer",
                "Submitted answer option is out of range.",
            )
        correct_idx = int(q["correct_index"])
        is_correct = selected == correct_idx
        if is_correct:
            correct_count += 1
        results.append(
            PracticeQuizQuestionResult(
                question_id=qid,
                prompt=str(q["prompt"]),
                options=options,
                selected_index=selected,
                correct_index=correct_idx,
                is_correct=is_correct,
                explanation=str(q["explanation"]),
            )
        )

    total = len(raw_questions)
    percentage = round((correct_count / total) * 100)
    attempt = PracticeQuizAttemptRecord(
        attempt_id=uuid4(),
        score=correct_count,
        total_questions=total,
        percentage=percentage,
        submitted_at=datetime.now(UTC),
        results=tuple(results),
    )

    updated_data = dict(structured_data)
    existing_attempts = list(updated_data.get("attempts") or [])
    existing_attempts.insert(0, attempt.model_dump(mode="json"))
    updated_data["attempts"] = existing_attempts[:10]
    return attempt, updated_data


_STEM_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_question_stem(prompt: str) -> str:
    return " ".join(_STEM_TOKEN_RE.findall(prompt.lower()))


def is_duplicate_question(candidate_prompt: str, existing_prompts: Sequence[str]) -> bool:
    """Detect exact normalized matches and high-overlap paraphrases across exam batches."""
    cand_norm = normalize_question_stem(candidate_prompt)
    if not cand_norm:
        return True
    cand_tokens = set(cand_norm.split())
    for prev in existing_prompts:
        prev_norm = normalize_question_stem(prev)
        if not prev_norm:
            continue
        if cand_norm == prev_norm:
            return True
        prev_tokens = set(prev_norm.split())
        union = len(cand_tokens | prev_tokens)
        if union > 0 and (len(cand_tokens & prev_tokens) / union) >= 0.82:
            return True
    return False


def build_practice_exam_batch_prompt(
    difficulty: QuizDifficulty,
    batch_size: int,
    *,
    existing_stems: Sequence[str] = (),
) -> str:
    level_guidance = {
        "easy": "Focus on foundational definitions, direct recall, and straightforward single-step checks.",
        "medium": "Focus on conceptual understanding, relationships, and short applied problems.",
        "hard": "Focus on multi-step reasoning, subtle distinctions, edge cases, and synthesis.",
    }[difficulty]
    avoid_clause = ""
    if existing_stems:
        recent = " | ".join(
            _clean_text(stem, max_len=70) for stem in existing_stems[-12:]
        )
        avoid_clause = f"\n- DO NOT repeat or paraphrase these already-written questions: {recent}"
    return (
        f"You are VIORA, an academic tutor writing a {difficulty}-level practice exam batch as compact JSON.\n"
        "Return ONLY valid JSON (no markdown fences, no commentary) matching this exact shape:\n"
        '{"title":"Practice Exam","questions":[{"topic":"Subtopic","prompt":"Question text?","options":["Option A","Option B","Option C","Option D"],"correct_index":0,"explanation":"Brief 1-sentence reason."}]}\n'
        "RULES:\n"
        f"- Difficulty: {difficulty}. {level_guidance}\n"
        f"- Produce EXACTLY {batch_size} distinct questions, each with 4 distinct options and integer correct_index (0, 1, 2, or 3).\n"
        "- Include a concise 2-4 word 'topic' subtopic label for every question.\n"
        "- Keep prompt <= 120 chars, each option <= 55 chars, explanation <= 110 chars so JSON fits within 400 tokens.\n"
        "- Prefer clean Unicode math (μ, σ, →, ∞, Σ) or double-escaped LaTeX (\\\\frac, \\\\mu) in JSON strings.\n"
        "- NO executable code unless the topic is programming. Ground questions in the provided study context."
        f"{avoid_clause}"
    )


def build_practice_exam_payload(
    *,
    title: str,
    difficulty: QuizDifficulty,
    question_count: int,
    duration_minutes: int,
    questions: Sequence[dict[str, Any]],
    fallback_topic: str | None = None,
) -> dict[str, Any]:
    default_topic = _clean_text(fallback_topic or "", max_len=60) or "General"
    normalized_questions: list[dict[str, Any]] = []
    for idx, q in enumerate(questions[:question_count], start=1):
        topic_tag = _clean_text(str(q.get("topic_tag") or ""), max_len=60)
        if not topic_tag or topic_tag == "General":
            topic_tag = default_topic
        normalized_questions.append(
            {
                "question_id": f"q{idx}",
                "prompt": str(q["prompt"]),
                "options": [str(opt) for opt in q["options"]],
                "correct_index": int(q["correct_index"]),
                "explanation": str(q["explanation"]),
                "topic_tag": topic_tag,
            }
        )
    return {
        "title": _clean_text(title, max_len=120) or "Practice Exam",
        "difficulty": difficulty,
        "question_count": len(normalized_questions),
        "duration_minutes": int(duration_minutes),
        "questions": normalized_questions,
        "active_attempt": None,
        "attempts": [],
    }


def _project_active_attempt(
    raw_active: dict[str, Any] | None,
    *,
    now: datetime,
) -> PracticeExamActiveAttempt | None:
    if not raw_active:
        return None
    started_at = datetime.fromisoformat(str(raw_active["started_at"]))
    expires_at = datetime.fromisoformat(str(raw_active["expires_at"]))
    remaining = max(0, int((expires_at - now).total_seconds()))
    return PracticeExamActiveAttempt(
        attempt_id=UUID(str(raw_active["attempt_id"])),
        started_at=started_at,
        expires_at=expires_at,
        server_now=now,
        remaining_seconds=remaining,
        answers={str(k): int(v) for k, v in (raw_active.get("answers") or {}).items()},
        marked_for_review=tuple(
            str(qid) for qid in (raw_active.get("marked_for_review") or ())
        ),
    )


def project_student_safe_exam(
    structured_data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> PracticeExamData:
    """Return exam questions stripped of correct_index/explanation until submission."""
    current = now or datetime.now(UTC)
    questions = tuple(
        PracticeExamQuestion(
            question_id=str(q["question_id"]),
            prompt=str(q["prompt"]),
            options=tuple(str(opt) for opt in q["options"]),
            topic_tag=str(q.get("topic_tag") or "General"),
        )
        for q in structured_data.get("questions", ())
    )
    attempts = tuple(
        PracticeExamAttemptRecord(**att)
        for att in structured_data.get("attempts", ())
    )
    active = _project_active_attempt(structured_data.get("active_attempt"), now=current)
    return PracticeExamData(
        title=str(structured_data.get("title") or "Practice Exam"),
        difficulty=structured_data.get("difficulty", "medium"),
        question_count=int(structured_data.get("question_count") or len(questions)),
        duration_minutes=int(structured_data.get("duration_minutes") or 15),
        questions=questions,
        active_attempt=active,
        attempts=attempts,
    )


def _validate_exam_answers(
    raw_questions: Sequence[dict[str, Any]],
    answers: dict[str, int],
) -> dict[str, int]:
    valid_qids = {str(q["question_id"]): len(q["options"]) for q in raw_questions}
    cleaned: dict[str, int] = {}
    for qid, selected in answers.items():
        key = str(qid)
        if key not in valid_qids:
            raise AccessError(
                422,
                "invalid_exam_question",
                f"Unknown question reference '{key}' in exam answers.",
            )
        idx = int(selected)
        if idx < 0 or idx >= valid_qids[key]:
            raise AccessError(
                422,
                "invalid_exam_answer",
                "Submitted answer option is out of range.",
            )
        cleaned[key] = idx
    return cleaned


def _finalize_exam_attempt(
    structured_data: dict[str, Any],
    *,
    active: dict[str, Any],
    final_answers: dict[str, int],
    submitted_at: datetime,
    timed_out: bool,
) -> tuple[PracticeExamAttemptRecord, dict[str, Any]]:
    raw_questions = list(structured_data.get("questions") or ())
    started_at = datetime.fromisoformat(str(active["started_at"]))
    expires_at = datetime.fromisoformat(str(active["expires_at"]))
    effective_end = min(submitted_at, expires_at) if timed_out else submitted_at
    duration_seconds = max(0, int((effective_end - started_at).total_seconds()))

    results: list[PracticeExamQuestionResult] = []
    topic_totals: dict[str, list[int]] = {}
    correct_count = 0

    for q in raw_questions:
        qid = str(q["question_id"])
        topic_tag = str(q.get("topic_tag") or "General")
        options = tuple(str(opt) for opt in q["options"])
        correct_idx = int(q["correct_index"])
        selected = final_answers.get(qid)
        is_correct = selected is not None and int(selected) == correct_idx
        if is_correct:
            correct_count += 1
        bucket = topic_totals.setdefault(topic_tag, [0, 0])
        bucket[1] += 1
        if is_correct:
            bucket[0] += 1
        results.append(
            PracticeExamQuestionResult(
                question_id=qid,
                topic_tag=topic_tag,
                prompt=str(q["prompt"]),
                options=options,
                selected_index=int(selected) if selected is not None else None,
                correct_index=correct_idx,
                is_correct=is_correct,
                explanation=str(q["explanation"]),
            )
        )

    total = len(raw_questions)
    percentage = round((correct_count / total) * 100) if total else 0
    topic_breakdown = tuple(
        PracticeExamTopicPerformance(
            topic=topic,
            correct=counts[0],
            total=counts[1],
            percentage=round((counts[0] / counts[1]) * 100),
        )
        for topic, counts in topic_totals.items()
    )

    attempt = PracticeExamAttemptRecord(
        attempt_id=UUID(str(active["attempt_id"])),
        score=correct_count,
        total_questions=total,
        percentage=percentage,
        started_at=started_at,
        submitted_at=submitted_at,
        duration_seconds=duration_seconds,
        timed_out=timed_out,
        topic_breakdown=topic_breakdown,
        results=tuple(results),
    )

    updated_data = dict(structured_data)
    updated_data["active_attempt"] = None
    existing_attempts = list(updated_data.get("attempts") or [])
    existing_attempts.insert(0, attempt.model_dump(mode="json"))
    updated_data["attempts"] = existing_attempts[:10]
    return attempt, updated_data


def start_practice_exam_attempt(
    structured_data: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[PracticeExamActiveAttempt, dict[str, Any]]:
    raw_questions = list(structured_data.get("questions") or ())
    if not raw_questions:
        raise AccessError(422, "invalid_exam", "Practice exam contains no questions")

    current = now or datetime.now(UTC)
    working_data = dict(structured_data)
    existing_active = working_data.get("active_attempt")
    if existing_active:
        expires_at = datetime.fromisoformat(str(existing_active["expires_at"]))
        if current <= expires_at:
            projected = _project_active_attempt(existing_active, now=current)
            assert projected is not None
            return projected, working_data
        # Existing attempt expired before explicit submit: auto-finalize server-saved answers first.
        saved_answers = {
            str(k): int(v)
            for k, v in (existing_active.get("answers") or {}).items()
        }
        _, working_data = _finalize_exam_attempt(
            working_data,
            active=existing_active,
            final_answers=saved_answers,
            submitted_at=expires_at,
            timed_out=True,
        )

    duration_minutes = max(5, min(60, int(working_data.get("duration_minutes") or 15)))
    expires_at = current + timedelta(minutes=duration_minutes)
    new_active = {
        "attempt_id": str(uuid4()),
        "started_at": current.isoformat(),
        "expires_at": expires_at.isoformat(),
        "answers": {},
        "marked_for_review": [],
    }
    working_data["active_attempt"] = new_active
    projected = _project_active_attempt(new_active, now=current)
    assert projected is not None
    return projected, working_data


def save_practice_exam_progress(
    structured_data: dict[str, Any],
    *,
    attempt_id: UUID,
    answers: dict[str, int],
    marked_for_review: Sequence[str],
    now: datetime | None = None,
) -> tuple[PracticeExamActiveAttempt, dict[str, Any]]:
    raw_questions = list(structured_data.get("questions") or ())
    active = structured_data.get("active_attempt")
    if not active or str(active.get("attempt_id")) != str(attempt_id):
        raise AccessError(
            409,
            "exam_attempt_not_active",
            "No active timed exam attempt matches this request.",
        )

    current = now or datetime.now(UTC)
    expires_at = datetime.fromisoformat(str(active["expires_at"]))
    if current > expires_at:
        raise AccessError(
            409,
            "exam_deadline_expired",
            "Exam deadline has expired; answers are locked for automatic submission.",
        )

    cleaned_answers = _validate_exam_answers(raw_questions, answers)
    valid_qids = {str(q["question_id"]) for q in raw_questions}
    cleaned_marks = [
        str(qid)
        for qid in dict.fromkeys(marked_for_review)
        if str(qid) in valid_qids
    ]

    updated_active = dict(active)
    updated_active["answers"] = cleaned_answers
    updated_active["marked_for_review"] = cleaned_marks

    updated_data = dict(structured_data)
    updated_data["active_attempt"] = updated_active
    projected = _project_active_attempt(updated_active, now=current)
    assert projected is not None
    return projected, updated_data


def score_practice_exam(
    structured_data: dict[str, Any],
    *,
    attempt_id: UUID,
    answers: dict[str, int],
    auto_submitted: bool = False,
    now: datetime | None = None,
) -> tuple[PracticeExamAttemptRecord, dict[str, Any]]:
    raw_questions = list(structured_data.get("questions") or ())
    if not raw_questions:
        raise AccessError(422, "invalid_exam", "Practice exam contains no questions")

    active = structured_data.get("active_attempt")
    if not active or str(active.get("attempt_id")) != str(attempt_id):
        raise AccessError(
            409,
            "exam_attempt_not_active",
            "No active timed exam attempt matches this submission.",
        )

    current = now or datetime.now(UTC)
    expires_at = datetime.fromisoformat(str(active["expires_at"]))
    grace = timedelta(seconds=3) if auto_submitted else timedelta(seconds=0)
    saved_answers = {
        str(k): int(v) for k, v in (active.get("answers") or {}).items()
    }

    if current <= expires_at + grace:
        merged = dict(saved_answers)
        if answers:
            merged.update(_validate_exam_answers(raw_questions, answers))
        timed_out = auto_submitted and (current >= expires_at - timedelta(seconds=1))
        final_answers = merged
    else:
        # Past deadline: reject post-deadline answer changes by grading server-autosaved answers only.
        timed_out = True
        final_answers = saved_answers

    return _finalize_exam_attempt(
        structured_data,
        active=active,
        final_answers=final_answers,
        submitted_at=current,
        timed_out=timed_out,
    )
