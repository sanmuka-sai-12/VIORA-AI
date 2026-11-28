"""Compact JSON generation, validation, and server-side grading for flashcards & quizzes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from ..security import AccessError
from .mind_map import _clean_text, loads_llm_json
from .schemas import (
    FlashcardDeck,
    FlashcardItem,
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
        if not prompt or not explanation or len(opts) != 4 or any(not o for o in opts):
            continue
        questions.append(
            {
                "question_id": f"q{idx}",
                "prompt": prompt,
                "options": opts,
                "correct_index": int(q.correct_index),
                "explanation": explanation,
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
