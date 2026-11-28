import { useState } from "react";
import katex from "katex";
import "katex/dist/katex.min.css";
import {
  parseAcademicBlocks,
  parseInlineSegments,
  type InlineSegment,
} from "./richContentParser";

type KatexRenderResult =
  | { ok: true; html: string }
  | { ok: false; fallback: string };

const LATEX_FALLBACK_SYMBOLS: ReadonlyArray<[RegExp, string]> = [
  [/\\cdot\b/g, " · "],
  [/\\times\b/g, " × "],
  [/\\div\b/g, " ÷ "],
  [/\\pm\b/g, " ± "],
  [/\\leq?\b/g, " ≤ "],
  [/\\geq?\b/g, " ≥ "],
  [/\\neq?\b/g, " ≠ "],
  [/\\approx\b/g, " ≈ "],
  [/\\rightarrow\b|\\to\b/g, " → "],
  [/\\infty\b/g, "∞"],
  [/\\left\s*/g, ""],
  [/\\right\s*/g, ""],
];

export function formatMathFallbackText(latex: string): string {
  let clean = latex.trim();
  clean = clean.replace(/\\frac\{([^{}]+)\}\{([^{}]+)\}/g, "($1)/($2)");
  clean = clean.replace(/\\sqrt\{([^{}]+)\}/g, "√($1)");
  for (const [pattern, replacement] of LATEX_FALLBACK_SYMBOLS) {
    clean = clean.replace(pattern, replacement);
  }
  return clean.replace(/\s{2,}/g, " ").trim();
}

export function renderKatexSafely(
  latex: string,
  displayMode: boolean,
): KatexRenderResult {
  const trimmed = latex.trim();
  if (!trimmed) {
    return { ok: false, fallback: "" };
  }
  try {
    const html = katex.renderToString(trimmed, {
      displayMode,
      throwOnError: true,
      strict: "ignore",
      output: "html",
    });
    if (!html || html.includes("katex-error")) {
      return { ok: false, fallback: formatMathFallbackText(trimmed) };
    }
    return { ok: true, html };
  } catch {
    return { ok: false, fallback: formatMathFallbackText(trimmed) };
  }
}

function InlineRichText({
  text,
  allowEmphasis = true,
}: {
  text: string;
  allowEmphasis?: boolean;
}) {
  const segments: InlineSegment[] = parseInlineSegments(text, allowEmphasis);
  return (
    <>
      {segments.map((seg, idx) => {
        if (seg.kind === "text") {
          return <span key={idx}>{seg.text}</span>;
        }
        if (seg.kind === "bold") {
          return (
            <strong key={idx}>
              <InlineRichText text={seg.text} allowEmphasis={false} />
            </strong>
          );
        }
        if (seg.kind === "italic") {
          return (
            <em key={idx}>
              <InlineRichText text={seg.text} allowEmphasis={false} />
            </em>
          );
        }
        if (seg.kind === "code") {
          return (
            <code key={idx} className="viora-inline-code">
              {seg.text}
            </code>
          );
        }
        const rendered = renderKatexSafely(seg.latex, false);
        if (!rendered.ok) {
          return (
            <code key={idx} className="viora-math-fallback-inline">
              {rendered.fallback}
            </code>
          );
        }
        return (
          <span
            key={idx}
            className="viora-math-inline"
            dangerouslySetInnerHTML={{
              __html: rendered.html,
            }}
          />
        );
      })}
    </>
  );
}

function CodeBlock({ language, code }: { language: string; code: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      // Clipboard may be unavailable outside secure contexts.
    }
  };

  return (
    <div className="viora-code-block">
      <div className="viora-code-header">
        <span>{language || "code"}</span>
        <button
          type="button"
          className="viora-btn viora-btn-sm"
          style={{ padding: "0.15rem 0.5rem", fontSize: "0.72rem" }}
          onClick={() => void handleCopy()}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre className="viora-code-pre">
        <code>{code}</code>
      </pre>
    </div>
  );
}

export function RichAcademicContent({ content }: { content: string }) {
  const blocks = parseAcademicBlocks(content);

  return (
    <div className="viora-rich-content">
      {blocks.map((block, idx) => {
        if (block.kind === "heading") {
          if (block.level === 1) {
            return (
              <h3 key={idx} className="viora-rich-h1">
                <InlineRichText text={block.text} />
              </h3>
            );
          }
          if (block.level === 2) {
            return (
              <h4 key={idx} className="viora-rich-h2">
                <InlineRichText text={block.text} />
              </h4>
            );
          }
          return (
            <h5 key={idx} className="viora-rich-h3">
              <InlineRichText text={block.text} />
            </h5>
          );
        }

        if (block.kind === "paragraph") {
          return (
            <p key={idx} className="viora-rich-p">
              <InlineRichText text={block.text} />
            </p>
          );
        }

        if (block.kind === "blockquote") {
          return (
            <blockquote key={idx} className="viora-rich-quote">
              <InlineRichText text={block.text} />
            </blockquote>
          );
        }

        if (block.kind === "divider") {
          return (
            <hr
              key={idx}
              style={{
                border: "none",
                borderTop: "1px solid var(--color-border)",
                margin: "0.25rem 0",
              }}
            />
          );
        }

        if (block.kind === "code") {
          return (
            <CodeBlock
              key={idx}
              language={block.language}
              code={block.code}
            />
          );
        }

        if (block.kind === "math_block") {
          const rendered = renderKatexSafely(block.latex, true);
          if (!rendered.ok) {
            return (
              <div key={idx} className="viora-math-fallback-block">
                <code>{rendered.fallback}</code>
              </div>
            );
          }
          return (
            <div
              key={idx}
              className="viora-math-block"
              dangerouslySetInnerHTML={{
                __html: rendered.html,
              }}
            />
          );
        }

        if (block.kind === "ul") {
          return (
            <ul key={idx} className="viora-rich-list">
              {block.items.map((item, itemIdx) => (
                <li key={itemIdx}>
                  <InlineRichText text={item} />
                </li>
              ))}
            </ul>
          );
        }

        return (
          <ol key={idx} className="viora-rich-list">
            {block.items.map((item, itemIdx) => (
              <li key={itemIdx}>
                <InlineRichText text={item} />
              </li>
            ))}
          </ol>
        );
      })}
    </div>
  );
}
