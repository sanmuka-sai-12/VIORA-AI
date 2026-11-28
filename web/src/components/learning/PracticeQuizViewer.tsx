import { useEffect, useState } from "react";
import type {
  PracticeQuizAttemptRecord,
  PracticeQuizData,
} from "../../api/client";
import { RichAcademicContent } from "../common/RichAcademicContent";
import { StatusBadge } from "../common/UiPrimitives";

const OPTION_LETTERS = ["A", "B", "C", "D"] as const;

export interface PracticeQuizViewerProps {
  quiz: PracticeQuizData;
  busy?: boolean;
  onSubmitAnswers: (
    answers: Record<string, number>,
  ) => Promise<PracticeQuizAttemptRecord | null>;
}

export function PracticeQuizViewer({
  quiz,
  busy = false,
  onSubmitAnswers,
}: PracticeQuizViewerProps) {
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [submitting, setSubmitting] = useState(false);
  const [takingNewAttempt, setTakingNewAttempt] = useState(
    () => quiz.attempts.length === 0,
  );
  const [selectedAttemptIdx, setSelectedAttemptIdx] = useState(0);
  const [incorrectOnly, setIncorrectOnly] = useState(false);

  useEffect(() => {
    setAnswers({});
    setSelectedAttemptIdx(0);
    setTakingNewAttempt(quiz.attempts.length === 0);
  }, [quiz]);

  const activeAttempt =
    quiz.attempts[selectedAttemptIdx] ?? quiz.attempts[0] ?? null;
  const allAnswered =
    quiz.questions.length > 0 &&
    quiz.questions.every((q) => answers[q.question_id] !== undefined);

  const handleSubmit = async () => {
    if (!allAnswered || submitting || busy) return;
    setSubmitting(true);
    try {
      const result = await onSubmitAnswers(answers);
      if (result) {
        setSelectedAttemptIdx(0);
        setTakingNewAttempt(false);
        setIncorrectOnly(false);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="viora-quiz-shell">
      <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
          <StatusBadge tone="brand">
            Difficulty: {quiz.difficulty.charAt(0).toUpperCase() + quiz.difficulty.slice(1)}
          </StatusBadge>
          <StatusBadge tone="neutral">{quiz.questions.length} Questions</StatusBadge>
          {quiz.attempts.length > 0 && (
            <StatusBadge tone="success">
              {quiz.attempts.length} Saved Attempt{quiz.attempts.length === 1 ? "" : "s"}
            </StatusBadge>
          )}
        </div>

        <div className="viora-row" style={{ gap: "0.35rem" }}>
          {!takingNewAttempt && (
            <button
              type="button"
              className="viora-btn viora-btn-sm viora-btn-primary"
              disabled={busy || submitting}
              onClick={() => {
                setAnswers({});
                setTakingNewAttempt(true);
              }}
            >
              Retake Quiz
            </button>
          )}
          {takingNewAttempt && activeAttempt && (
            <button
              type="button"
              className="viora-btn viora-btn-sm"
              onClick={() => setTakingNewAttempt(false)}
            >
              View Saved Attempts
            </button>
          )}
        </div>
      </div>

      {takingNewAttempt ? (
        <div className="viora-quiz-questions">
          {quiz.questions.map((q, qIdx) => {
            const chosen = answers[q.question_id];
            return (
              <div key={q.question_id} className="viora-quiz-question">
                <div style={{ fontWeight: 600, marginBottom: "0.45rem" }}>
                  <span>{qIdx + 1}. </span>
                  <RichAcademicContent content={q.prompt} />
                </div>
                <div className="viora-quiz-options">
                  {q.options.map((opt, oIdx) => {
                    const selected = chosen === oIdx;
                    return (
                      <button
                        key={oIdx}
                        type="button"
                        className={`viora-quiz-option${selected ? " is-selected" : ""}`}
                        disabled={submitting || busy}
                        onClick={() =>
                          setAnswers((prev) => ({
                            ...prev,
                            [q.question_id]: oIdx,
                          }))
                        }
                      >
                        <strong style={{ minWidth: "1.4rem" }}>
                          {OPTION_LETTERS[oIdx] ?? oIdx + 1}.
                        </strong>
                        <span style={{ textAlign: "left" }}>
                          <RichAcademicContent content={opt} />
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}

          <div className="viora-row" style={{ justifyContent: "space-between" }}>
            <span className="viora-field-hint">
              {Object.keys(answers).length} of {quiz.questions.length} answered
            </span>
            <button
              type="button"
              className="viora-btn viora-btn-primary"
              disabled={!allAnswered || submitting || busy}
              onClick={() => void handleSubmit()}
            >
              {submitting ? "Scoring..." : "Submit Practice Quiz"}
            </button>
          </div>
        </div>
      ) : activeAttempt ? (
        <div className="viora-quiz-review">
          <div className="viora-quiz-score-banner">
            <div className="viora-row" style={{ gap: "0.6rem", flexWrap: "wrap" }}>
              <strong style={{ fontSize: "1.05rem", color: "var(--color-brand-primary)" }}>
                Score: {activeAttempt.score} / {activeAttempt.total_questions} ({activeAttempt.percentage}%)
              </strong>
              <StatusBadge
                tone={
                  activeAttempt.percentage >= 80
                    ? "success"
                    : activeAttempt.percentage >= 50
                      ? "warning"
                      : "danger"
                }
              >
                {activeAttempt.percentage >= 80
                  ? "Strong Mastery"
                  : activeAttempt.percentage >= 50
                    ? "Good Progress"
                    : "Needs Review"}
              </StatusBadge>
            </div>

            <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
              {quiz.attempts.length > 1 && (
                <select
                  aria-label="Select saved quiz attempt"
                  className="viora-select"
                  style={{ width: "auto", padding: "0.25rem 0.5rem", fontSize: "0.78rem" }}
                  value={selectedAttemptIdx}
                  onChange={(e) => setSelectedAttemptIdx(Number(e.target.value))}
                >
                  {quiz.attempts.map((att, idx) => (
                    <option key={att.attempt_id} value={idx}>
                      Attempt {quiz.attempts.length - idx}: {att.score}/{att.total_questions} ({att.percentage}%)
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className={`viora-btn viora-btn-sm ${incorrectOnly ? "viora-btn-primary" : ""}`}
                onClick={() => setIncorrectOnly((prev) => !prev)}
              >
                {incorrectOnly ? "Show All" : "Incorrect Only"}
              </button>
            </div>
          </div>

          <div className="viora-quiz-questions">
            {activeAttempt.results
              .filter((r) => !incorrectOnly || !r.is_correct)
              .map((res, idx) => (
                <div
                  key={res.question_id}
                  className={`viora-quiz-question ${res.is_correct ? "is-correct" : "is-incorrect"}`}
                >
                  <div className="viora-row" style={{ justifyContent: "space-between", marginBottom: "0.35rem" }}>
                    <strong>Question {idx + 1}</strong>
                    <StatusBadge tone={res.is_correct ? "success" : "danger"}>
                      {res.is_correct ? "Correct" : "Incorrect"}
                    </StatusBadge>
                  </div>
                  <div style={{ marginBottom: "0.45rem" }}>
                    <RichAcademicContent content={res.prompt} />
                  </div>
                  <div className="viora-quiz-options">
                    {res.options.map((opt, oIdx) => {
                      const isCorrectOpt = oIdx === res.correct_index;
                      const isChosenOpt = oIdx === res.selected_index;
                      const stateClass = isCorrectOpt
                        ? " is-answer-correct"
                        : isChosenOpt
                          ? " is-answer-wrong"
                          : "";
                      return (
                        <div
                          key={oIdx}
                          className={`viora-quiz-option is-static${stateClass}`}
                        >
                          <strong style={{ minWidth: "1.4rem" }}>
                            {OPTION_LETTERS[oIdx] ?? oIdx + 1}.
                          </strong>
                          <span style={{ flex: 1 }}>
                            <RichAcademicContent content={opt} />
                          </span>
                          {isCorrectOpt && <StatusBadge tone="success">Correct</StatusBadge>}
                          {isChosenOpt && !isCorrectOpt && (
                            <StatusBadge tone="danger">Your Answer</StatusBadge>
                          )}
                        </div>
                      );
                    })}
                  </div>
                  <div className="viora-quiz-explanation">
                    <strong>Explanation: </strong>
                    <RichAcademicContent content={res.explanation} />
                  </div>
                </div>
              ))}
            {incorrectOnly && activeAttempt.results.every((r) => r.is_correct) && (
              <p className="viora-field-hint">
                All questions were answered correctly in this attempt.
              </p>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
}
