import { useMemo, useState } from "react";
import type { FlashcardDeck, FlashcardStatus } from "../../api/client";
import { RichAcademicContent } from "../common/RichAcademicContent";
import { StatusBadge } from "../common/UiPrimitives";

export interface FlashcardDeckViewerProps {
  deck: FlashcardDeck;
  busy?: boolean;
  onUpdateStatuses: (statuses: Record<string, FlashcardStatus>) => Promise<void>;
}

export function FlashcardDeckViewer({
  deck,
  busy = false,
  onUpdateStatuses,
}: FlashcardDeckViewerProps) {
  const [filterReviewOnly, setFilterReviewOnly] = useState(false);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);
  const [showHint, setShowHint] = useState(false);

  const knownCount = useMemo(
    () => deck.cards.filter((c) => c.status === "known").length,
    [deck.cards],
  );
  const unknownCount = useMemo(
    () => deck.cards.filter((c) => c.status === "unknown").length,
    [deck.cards],
  );

  const visibleCards = useMemo(() => {
    if (!filterReviewOnly) return deck.cards;
    const filtered = deck.cards.filter((c) => c.status !== "known");
    return filtered.length > 0 ? filtered : deck.cards;
  }, [deck.cards, filterReviewOnly]);

  const safeIndex = Math.min(index, Math.max(0, visibleCards.length - 1));
  const currentCard = visibleCards[safeIndex];
  const progressPct =
    deck.cards.length > 0 ? Math.round((knownCount / deck.cards.length) * 100) : 0;

  if (!currentCard) {
    return null;
  }

  const goTo = (nextIdx: number) => {
    setIndex(nextIdx);
    setFlipped(false);
    setShowHint(false);
  };

  const handleMark = async (status: FlashcardStatus) => {
    await onUpdateStatuses({ [currentCard.card_id]: status });
    if (safeIndex + 1 < visibleCards.length) {
      goTo(safeIndex + 1);
    } else {
      setFlipped(false);
    }
  };

  const handleResetAll = async () => {
    const resetMap: Record<string, FlashcardStatus> = {};
    for (const card of deck.cards) {
      resetMap[card.card_id] = "unseen";
    }
    setFilterReviewOnly(false);
    goTo(0);
    await onUpdateStatuses(resetMap);
  };

  return (
    <div className="viora-flashcards-shell">
      <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="viora-row" style={{ gap: "0.4rem", flexWrap: "wrap" }}>
          <StatusBadge tone="success">{knownCount} Known</StatusBadge>
          <StatusBadge tone="warning">{unknownCount} Needs Review</StatusBadge>
          <span className="viora-field-hint">
            Progress: {progressPct}% ({knownCount}/{deck.cards.length})
          </span>
        </div>

        <div className="viora-row" style={{ gap: "0.35rem" }}>
          <button
            type="button"
            className={`viora-btn viora-btn-sm ${filterReviewOnly ? "viora-btn-primary" : ""}`}
            disabled={busy}
            onClick={() => {
              setFilterReviewOnly((prev) => !prev);
              goTo(0);
            }}
          >
            {filterReviewOnly ? "Showing Unmastered" : "Review Unmastered"}
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            disabled={busy || (knownCount === 0 && unknownCount === 0)}
            onClick={() => void handleResetAll()}
          >
            Reset Progress
          </button>
        </div>
      </div>

      <div className="viora-progress-track" aria-hidden="true">
        <div
          className="viora-progress-fill"
          style={{ width: `${progressPct}%` }}
        />
      </div>

      <div
        className={`viora-flashcard-stage${flipped ? " is-flipped" : ""}`}
        role="button"
        tabIndex={0}
        aria-label={`Flashcard ${safeIndex + 1} of ${visibleCards.length}. Click to flip.`}
        onClick={() => setFlipped((prev) => !prev)}
        onKeyDown={(e) => {
          if (e.key === " " || e.key === "Enter") {
            e.preventDefault();
            setFlipped((prev) => !prev);
          }
        }}
      >
        <div className="viora-row" style={{ justifyContent: "space-between", width: "100%" }}>
          <StatusBadge tone={flipped ? "brand" : "neutral"}>
            {flipped ? "Answer (Back)" : "Prompt (Front)"}
          </StatusBadge>
          <StatusBadge
            tone={
              currentCard.status === "known"
                ? "success"
                : currentCard.status === "unknown"
                  ? "warning"
                  : "neutral"
            }
          >
            {currentCard.status === "known"
              ? "Known"
              : currentCard.status === "unknown"
                ? "Needs Review"
                : "Unseen"}
          </StatusBadge>
        </div>

        <div className="viora-flashcard-body">
          <RichAcademicContent
            content={flipped ? currentCard.back : currentCard.front}
          />
        </div>

        <div className="viora-field-hint">
          Click card or press Space to {flipped ? "see front" : "reveal answer"}
        </div>
      </div>

      {!flipped && currentCard.hint && (
        <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            onClick={() => setShowHint((prev) => !prev)}
          >
            {showHint ? "Hide Hint" : "Show Hint"}
          </button>
          {showHint && (
            <div style={{ flex: 1, fontSize: "0.84rem", color: "var(--color-text-muted)" }}>
              <RichAcademicContent content={currentCard.hint} />
            </div>
          )}
        </div>
      )}

      <div className="viora-row" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
        <div className="viora-row">
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            disabled={safeIndex === 0}
            onClick={() => goTo(safeIndex - 1)}
          >
            Previous
          </button>
          <span className="viora-field-hint">
            Card {safeIndex + 1} of {visibleCards.length}
          </span>
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            disabled={safeIndex >= visibleCards.length - 1}
            onClick={() => goTo(safeIndex + 1)}
          >
            Next
          </button>
        </div>

        <div className="viora-row">
          <button
            type="button"
            className="viora-btn viora-btn-sm"
            disabled={busy}
            onClick={() => void handleMark("unknown")}
          >
            Needs Review
          </button>
          <button
            type="button"
            className="viora-btn viora-btn-sm viora-btn-primary"
            disabled={busy}
            onClick={() => void handleMark("known")}
          >
            Mark Known
          </button>
        </div>
      </div>
    </div>
  );
}
