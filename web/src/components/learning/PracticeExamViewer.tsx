import { useEffect, useRef, useState } from "react";
import type {
  PracticeExamActiveAttempt,
  PracticeExamAttemptRecord,
  PracticeExamData,
} from "../../api/client";
import { RichAcademicContent } from "../common/RichAcademicContent";
import { StatusBadge } from "../common/UiPrimitives";

const OPTION_LETTERS = ["A", "B", "C", "D"] as const;

function formatClock(totalSeconds: number): string {
  const safe = Math.max(0, Math.floor(totalSeconds));
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export interface PracticeExamViewerProps {
  exam: PracticeExamData;
  busy?: boolean;
  onStartExam: () => Promise<PracticeExamActiveAttempt | null>;
  onSaveProgress: (
    attemptId: string,
    answers: Record<string, number>,
    markedForReview: string[],
  ) => Promise<PracticeExamActiveAttempt | null>;
  onSubmitExam: (
    attemptId: string,
    answers: Record<string, number>,
    autoSubmitted: boolean,
  ) => Promise<PracticeExamAttemptRecord | null>;
}

export function PracticeExamViewer({
  exam,
  busy = false,
  onStartExam,
  onSaveProgress,
  onSubmitExam,
}: PracticeExamViewerProps) {
  const [activeAttempt, setActiveAttempt] =
    useState<PracticeExamActiveAttempt | null>(() => exam.active_attempt ?? null);
  const [answers, setAnswers] = useState<Record<string, number>>(
    () => exam.active_attempt?.answers ?? {},
  );
  const [marked, setMarked] = useState<string[]>(
    () => exam.active_attempt?.marked_for_review ?? [],
  );
  const [currentIdx, setCurrentIdx] = useState(0);
  const [remainingSeconds, setRemainingSeconds] = useState<number>(
    () => exam.active_attempt?.remaining_seconds ?? exam.duration_minutes * 60,
  );
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved">("idle");
  const [starting, setStarting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [selectedAttemptIdx, setSelectedAttemptIdx] = useState(0);
  const [incorrectOnly, setIncorrectOnly] = useState(false);

  const deadlineMsRef = useRef<number>(0);
  const clockOffsetMsRef = useRef<number>(0);
  const autoSubmittedAttemptRef = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const answersRef = useRef(answers);
  const markedRef = useRef(marked);
  answersRef.current = answers;
  markedRef.current = marked;

  useEffect(() => {
    const nextActive = exam.active_attempt ?? null;
    setActiveAttempt(nextActive);
    setAnswers(nextActive?.answers ?? {});
    setMarked(nextActive?.marked_for_review ?? []);
    setCurrentIdx(0);
    setSelectedAttemptIdx(0);
    setSaveState("idle");
    dirtyRef.current = false;
    if (nextActive) {
      const serverNowMs = Date.parse(nextActive.server_now);
      const expiresMs = Date.parse(nextActive.expires_at);
      clockOffsetMsRef.current = Number.isNaN(serverNowMs)
        ? 0
        : serverNowMs - Date.now();
      deadlineMsRef.current = Number.isNaN(expiresMs)
        ? Date.now() + nextActive.remaining_seconds * 1000
        : expiresMs;
      setRemainingSeconds(nextActive.remaining_seconds);
    }
  }, [exam]);

  const handleSubmit = async (autoSubmitted: boolean) => {
    if (!activeAttempt || submitting || busy) return;
    setSubmitting(true);
    try {
      const record = await onSubmitExam(
        activeAttempt.attempt_id,
        answersRef.current,
        autoSubmitted,
      );
      if (record) {
        setActiveAttempt(null);
        setSelectedAttemptIdx(0);
        setIncorrectOnly(false);
        dirtyRef.current = false;
      }
    } finally {
      setSubmitting(false);
    }
  };

  // Live countdown timer & automatic submission at deadline expiry.
  useEffect(() => {
    if (!activeAttempt) return;
    const tick = () => {
      const nowServerMs = Date.now() + clockOffsetMsRef.current;
      const secsLeft = Math.max(
        0,
        Math.ceil((deadlineMsRef.current - nowServerMs) / 1000),
      );
      setRemainingSeconds(secsLeft);
      if (
        secsLeft <= 0 &&
        autoSubmittedAttemptRef.current !== activeAttempt.attempt_id
      ) {
        autoSubmittedAttemptRef.current = activeAttempt.attempt_id;
        void handleSubmit(true);
      }
    };
    tick();
    const timerId = window.setInterval(tick, 1000);
    return () => window.clearInterval(timerId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeAttempt?.attempt_id]);

  // Debounced autosave of answers and marked-for-review flags.
  useEffect(() => {
    if (!activeAttempt || !dirtyRef.current || submitting) return;
    setSaveState("saving");
    const timer = window.setTimeout(async () => {
      const updated = await onSaveProgress(
        activeAttempt.attempt_id,
        answersRef.current,
        markedRef.current,
      );
      if (updated) {
        dirtyRef.current = false;
        setSaveState("saved");
      }
    }, 550);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answers, marked, activeAttempt?.attempt_id]);

  const handleStart = async () => {
    if (starting || busy || submitting) return;
    setStarting(true);
    try {
      const started = await onStartExam();
      if (started) {
        autoSubmittedAttemptRef.current = null;
        const serverNowMs = Date.parse(started.server_now);
        const expiresMs = Date.parse(started.expires_at);
        clockOffsetMsRef.current = Number.isNaN(serverNowMs)
          ? 0
          : serverNowMs - Date.now();
        deadlineMsRef.current = Number.isNaN(expiresMs)
          ? Date.now() + started.remaining_seconds * 1000
          : expiresMs;
        setActiveAttempt(started);
        setAnswers(started.answers ?? {});
        setMarked(started.marked_for_review ?? []);
        setRemainingSeconds(started.remaining_seconds);
        setCurrentIdx(0);
        setSaveState("idle");
        dirtyRef.current = false;
      }
    } finally {
      setStarting(false);
    }
  };

  const currentQuestion = exam.questions[currentIdx] ?? exam.questions[0];
  const answeredCount = exam.questions.filter(
    (q) => answers[q.question_id] !== undefined,
  ).length;
  const activeReviewAttempt =
    exam.attempts[selectedAttemptIdx] ?? exam.attempts[0] ?? null;

  return (
    <div className="viora-quiz-shell">
      <div
        className="viora-row"
        style={{ justifyContent: "space-between", flexWrap: "wrap" }}
      >
        <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
          <StatusBadge tone="brand">
            {exam.difficulty.charAt(0).toUpperCase() + exam.difficulty.slice(1)} Exam
          </StatusBadge>
          <StatusBadge tone="neutral">{exam.question_count} Questions</StatusBadge>
          <StatusBadge tone="neutral">{exam.duration_minutes} min</StatusBadge>
          {exam.attempts.length > 0 && (
            <StatusBadge tone="success">
              {exam.attempts.length} Completed Attempt
              {exam.attempts.length === 1 ? "" : "s"}
            </StatusBadge>
          )}
        </div>

        {!activeAttempt ? (
          <button
            type="button"
            className="viora-btn viora-btn-sm viora-btn-primary"
            disabled={busy || starting}
            onClick={() => void handleStart()}
          >
            {starting
              ? "Starting..."
              : exam.attempts.length > 0
                ? "Retake Timed Exam"
                : "Start Timed Exam"}
          </button>
        ) : (
          <div className="viora-row" style={{ gap: "0.45rem", flexWrap: "wrap" }}>
            <StatusBadge tone={remainingSeconds <= 60 ? "danger" : "warning"}>
              Time Left: {formatClock(remainingSeconds)}
            </StatusBadge>
            <StatusBadge tone="neutral">
              {answeredCount}/{exam.questions.length} Answered
            </StatusBadge>
            {saveState === "saving" && (
              <span className="viora-field-hint">Saving...</span>
            )}
            {saveState === "saved" && (
              <span className="viora-field-hint">Autosaved</span>
            )}
          </div>
        )}
      </div>

      {activeAttempt && currentQuestion ? (
        <div style={{ display: "grid", gap: "0.85rem", marginTop: "0.75rem" }}>
          {/* Question Navigator */}
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              gap: "0.35rem",
              padding: "0.55rem",
              borderRadius: "var(--radius-sm)",
              background: "var(--color-surface-subtle, rgba(15, 23, 42, 0.03))",
              border: "1px solid var(--color-border)",
            }}
            aria-label="Exam question navigator"
          >
            {exam.questions.map((q, idx) => {
              const isCurrent = idx === currentIdx;
              const isAnswered = answers[q.question_id] !== undefined;
              const isMarked = marked.includes(q.question_id);
              return (
                <button
                  key={q.question_id}
                  type="button"
                  onClick={() => setCurrentIdx(idx)}
                  title={`Question ${idx + 1}${isMarked ? " (Marked for review)" : ""}${isAnswered ? " (Answered)" : ""}`}
                  style={{
                    minWidth: "2rem",
                    height: "2rem",
                    padding: "0 0.45rem",
                    borderRadius: "6px",
                    fontSize: "0.78rem",
                    fontWeight: isCurrent ? 700 : 600,
                    cursor: "pointer",
                    border: isCurrent
                      ? "2px solid var(--color-brand-primary)"
                      : isMarked
                        ? "1.5px solid #d97706"
                        : "1px solid var(--color-border)",
                    background: isMarked
                      ? "rgba(245, 158, 11, 0.16)"
                      : isAnswered
                        ? "rgba(37, 99, 235, 0.12)"
                        : "var(--color-surface)",
                    color: "var(--color-text)",
                  }}
                >
                  {idx + 1}
                  {isMarked ? "*" : ""}
                </button>
              );
            })}
          </div>

          {/* Active Question Card */}
          <div className="viora-quiz-question">
            <div
              className="viora-row"
              style={{
                justifyContent: "space-between",
                marginBottom: "0.45rem",
                flexWrap: "wrap",
              }}
            >
              <div className="viora-row" style={{ gap: "0.4rem" }}>
                <span className="viora-quiz-qnum">
                  Question {currentIdx + 1} of {exam.questions.length}
                </span>
                <StatusBadge tone="neutral">{currentQuestion.topic_tag}</StatusBadge>
              </div>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                onClick={() => {
                  dirtyRef.current = true;
                  setMarked((prev) =>
                    prev.includes(currentQuestion.question_id)
                      ? prev.filter((id) => id !== currentQuestion.question_id)
                      : [...prev, currentQuestion.question_id],
                  );
                }}
              >
                {marked.includes(currentQuestion.question_id)
                  ? "Unmark Review"
                  : "Mark for Review"}
              </button>
            </div>

            <div className="viora-quiz-prompt">
              <RichAcademicContent content={currentQuestion.prompt} />
            </div>

            <div className="viora-quiz-options">
              {currentQuestion.options.map((opt, optIdx) => {
                const selected =
                  answers[currentQuestion.question_id] === optIdx;
                return (
                  <label
                    key={`${currentQuestion.question_id}-${optIdx}`}
                    className={`viora-quiz-option ${selected ? "is-selected" : ""}`}
                  >
                    <input
                      type="radio"
                      name={`exam-${currentQuestion.question_id}`}
                      checked={selected}
                      onChange={() => {
                        dirtyRef.current = true;
                        setAnswers((prev) => ({
                          ...prev,
                          [currentQuestion.question_id]: optIdx,
                        }));
                      }}
                    />
                    <span className="viora-quiz-letter">
                      {OPTION_LETTERS[optIdx] ?? optIdx + 1}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <RichAcademicContent content={opt} />
                    </div>
                  </label>
                );
              })}
            </div>
          </div>

          {/* Navigation & Submit Bar */}
          <div
            className="viora-row"
            style={{ justifyContent: "space-between", flexWrap: "wrap" }}
          >
            <div className="viora-row" style={{ gap: "0.4rem" }}>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={currentIdx === 0}
                onClick={() => setCurrentIdx((i) => Math.max(0, i - 1))}
              >
                Previous
              </button>
              <button
                type="button"
                className="viora-btn viora-btn-sm"
                disabled={currentIdx >= exam.questions.length - 1}
                onClick={() =>
                  setCurrentIdx((i) =>
                    Math.min(exam.questions.length - 1, i + 1),
                  )
                }
              >
                Next
              </button>
              {answers[currentQuestion.question_id] !== undefined && (
                <button
                  type="button"
                  className="viora-btn viora-btn-sm"
                  onClick={() => {
                    dirtyRef.current = true;
                    setAnswers((prev) => {
                      const copy = { ...prev };
                      delete copy[currentQuestion.question_id];
                      return copy;
                    });
                  }}
                >
                  Clear Answer
                </button>
              )}
            </div>

            <button
              type="button"
              className="viora-btn viora-btn-primary"
              disabled={submitting || busy}
              onClick={() => void handleSubmit(false)}
            >
              {submitting ? "Submitting..." : "Submit Exam"}
            </button>
          </div>
        </div>
      ) : activeReviewAttempt ? (
        <div className="viora-quiz-results" style={{ marginTop: "0.75rem" }}>
          <div
            className="viora-quiz-score-banner"
            style={{ flexWrap: "wrap", gap: "0.5rem" }}
          >
            <div className="viora-row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
              <strong>
                Score: {activeReviewAttempt.score}/{activeReviewAttempt.total_questions} (
                {activeReviewAttempt.percentage}%)
              </strong>
              <StatusBadge tone="neutral">
                Duration: {formatClock(activeReviewAttempt.duration_seconds)}
              </StatusBadge>
              {activeReviewAttempt.timed_out && (
                <StatusBadge tone="warning">
                  Auto-submitted at Deadline
                </StatusBadge>
              )}
              {exam.attempts.length > 1 && (
                <select
                  aria-label="Select exam attempt"
                  className="viora-select"
                  style={{ width: "auto", padding: "0.25rem 0.5rem", fontSize: "0.8rem" }}
                  value={selectedAttemptIdx}
                  onChange={(e) => setSelectedAttemptIdx(Number(e.target.value))}
                >
                  {exam.attempts.map((att, idx) => (
                    <option key={att.attempt_id} value={idx}>
                      Attempt {exam.attempts.length - idx} ({att.score}/
                      {att.total_questions} · {att.percentage}%)
                    </option>
                  ))}
                </select>
              )}
            </div>

            <label className="viora-check-inline">
              <input
                type="checkbox"
                checked={incorrectOnly}
                onChange={(e) => setIncorrectOnly(e.target.checked)}
              />
              <span>Incorrect only</span>
            </label>
          </div>

          {/* Topic-wise Performance Breakdown */}
          {activeReviewAttempt.topic_breakdown.length > 0 && (
            <div
              style={{
                display: "grid",
                gap: "0.45rem",
                marginTop: "0.75rem",
                padding: "0.65rem 0.75rem",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border)",
                background: "var(--color-surface)",
              }}
            >
              <strong style={{ fontSize: "0.85rem" }}>
                Topic-wise Performance
              </strong>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
                  gap: "0.45rem",
                }}
              >
                {activeReviewAttempt.topic_breakdown.map((tp) => (
                  <div
                    key={tp.topic}
                    style={{
                      padding: "0.45rem 0.55rem",
                      borderRadius: "6px",
                      border: "1px solid var(--color-border)",
                      fontSize: "0.8rem",
                    }}
                  >
                    <div
                      className="viora-row"
                      style={{ justifyContent: "space-between", gap: "0.35rem" }}
                    >
                      <span style={{ fontWeight: 600 }}>{tp.topic}</span>
                      <span>
                        {tp.correct}/{tp.total} ({tp.percentage}%)
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="viora-quiz-questions" style={{ marginTop: "0.75rem" }}>
            {activeReviewAttempt.results
              .filter((r) => !incorrectOnly || !r.is_correct)
              .map((res, idx) => (
                <div
                  key={res.question_id}
                  className={`viora-quiz-review-card ${
                    res.is_correct ? "is-correct" : "is-incorrect"
                  }`}
                >
                  <div
                    className="viora-row"
                    style={{ justifyContent: "space-between", flexWrap: "wrap" }}
                  >
                    <div className="viora-row" style={{ gap: "0.4rem" }}>
                      <span className="viora-quiz-qnum">Question {idx + 1}</span>
                      <StatusBadge tone="neutral">{res.topic_tag}</StatusBadge>
                    </div>
                    <StatusBadge tone={res.is_correct ? "success" : "danger"}>
                      {res.is_correct
                        ? "Correct"
                        : res.selected_index === null ||
                            res.selected_index === undefined
                          ? "Unanswered"
                          : "Incorrect"}
                    </StatusBadge>
                  </div>
                  <div className="viora-quiz-prompt">
                    <RichAcademicContent content={res.prompt} />
                  </div>
                  <div style={{ display: "grid", gap: "0.25rem", fontSize: "0.85rem" }}>
                    <div>
                      <strong>Your answer:</strong>{" "}
                      {res.selected_index === null ||
                      res.selected_index === undefined ? (
                        <em>Not answered</em>
                      ) : (
                        <>
                          {OPTION_LETTERS[res.selected_index]}.{" "}
                          <RichAcademicContent
                            content={res.options[res.selected_index] ?? ""}
                          />
                        </>
                      )}
                    </div>
                    {!res.is_correct && (
                      <div>
                        <strong>Correct answer:</strong>{" "}
                        {OPTION_LETTERS[res.correct_index]}.{" "}
                        <RichAcademicContent
                          content={res.options[res.correct_index] ?? ""}
                        />
                      </div>
                    )}
                    <div className="viora-quiz-explanation">
                      <strong>Explanation:</strong>{" "}
                      <RichAcademicContent content={res.explanation} />
                    </div>
                  </div>
                </div>
              ))}
          </div>
        </div>
      ) : (
        <p className="viora-field-hint" style={{ marginTop: "0.65rem" }}>
          Ready when you are: {exam.question_count} questions,{" "}
          {exam.duration_minutes}-minute server-enforced timer with autosave,
          question navigation, mark for review, and topic-wise breakdown.
        </p>
      )}
    </div>
  );
}
