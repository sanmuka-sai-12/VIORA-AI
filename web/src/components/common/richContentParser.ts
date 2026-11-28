export type ContentBlock =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "paragraph"; text: string }
  | { kind: "blockquote"; text: string }
  | { kind: "divider" }
  | { kind: "code"; language: string; code: string }
  | { kind: "math_block"; latex: string }
  | { kind: "ul"; items: string[] }
  | { kind: "ol"; items: string[] };

export type InlineSegment =
  | { kind: "text"; text: string }
  | { kind: "bold"; text: string }
  | { kind: "italic"; text: string }
  | { kind: "code"; text: string }
  | { kind: "math_inline"; latex: string };

function matchHeading(line: string): { level: 1 | 2 | 3; text: string } | null {
  const match =
    line.match(/^(#{1,6})\s+(.+?)\s*#*\s*$/) ??
    line.match(/^(#{2,6})([^\s#].*?)\s*#*\s*$/);
  if (!match) {
    return null;
  }
  const rawLevel = match[1].length;
  const level = (rawLevel >= 3 ? 3 : rawLevel) as 1 | 2 | 3;
  const text = match[2].trim();
  if (!text) {
    return null;
  }
  return { level, text };
}

/**
 * Splits raw academic markdown/LaTeX into structured blocks without executing HTML.
 */
export function parseAcademicBlocks(markdown: string): ContentBlock[] {
  const normalized = markdown.replace(/\r\n/g, "\n").trim();
  if (!normalized) {
    return [];
  }

  const lines = normalized.split("\n");
  const blocks: ContentBlock[] = [];
  let i = 0;

  const flushParagraph = (buf: string[]) => {
    const text = buf.join(" ").trim();
    if (text) {
      blocks.push({ kind: "paragraph", text });
    }
    buf.length = 0;
  };

  let paragraphBuf: string[] = [];

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Fenced code block ```lang
    if (trimmed.startsWith("```")) {
      flushParagraph(paragraphBuf);
      const lang = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        codeLines.push(lines[i]);
        i += 1;
      }
      blocks.push({
        kind: "code",
        language: lang,
        code: codeLines.join("\n"),
      });
      i += 1;
      continue;
    }

    // Display math block $$...$$ or \[...\]
    if (trimmed.startsWith("$$") || trimmed.startsWith("\\[")) {
      flushParagraph(paragraphBuf);
      const isBracket = trimmed.startsWith("\\[");
      const openToken = isBracket ? "\\[" : "$$";
      const closeToken = isBracket ? "\\]" : "$$";

      if (
        trimmed.length > openToken.length &&
        trimmed.endsWith(closeToken) &&
        trimmed.indexOf(closeToken, openToken.length) ===
          trimmed.length - closeToken.length
      ) {
        const inner = trimmed
          .slice(openToken.length, trimmed.length - closeToken.length)
          .trim();
        blocks.push({ kind: "math_block", latex: inner });
        i += 1;
        continue;
      }

      const mathLines: string[] = [trimmed.slice(openToken.length)];
      i += 1;
      while (i < lines.length) {
        const cur = lines[i];
        const closeIdx = cur.indexOf(closeToken);
        if (closeIdx !== -1) {
          mathLines.push(cur.slice(0, closeIdx));
          i += 1;
          break;
        }
        mathLines.push(cur);
        i += 1;
      }
      const latex = mathLines.join("\n").trim();
      if (latex) {
        blocks.push({ kind: "math_block", latex });
      }
      continue;
    }

    // Blank line
    if (!trimmed) {
      flushParagraph(paragraphBuf);
      i += 1;
      continue;
    }

    // Bare empty hash lines (e.g., stray "###")
    if (/^#{1,6}\s*$/.test(trimmed)) {
      flushParagraph(paragraphBuf);
      i += 1;
      continue;
    }

    // Setext heading: single paragraph line followed by === or ---
    if (
      paragraphBuf.length === 1 &&
      (/^[=]{3,}\s*$/.test(trimmed) || /^[-]{3,}\s*$/.test(trimmed))
    ) {
      const headingText = paragraphBuf.pop()!.trim();
      const level = trimmed.startsWith("=") ? 1 : 2;
      blocks.push({ kind: "heading", level, text: headingText });
      i += 1;
      continue;
    }

    // Horizontal divider --- / *** / ___
    if (/^(?:[-*_]\s*){3,}$/.test(trimmed)) {
      flushParagraph(paragraphBuf);
      blocks.push({ kind: "divider" });
      i += 1;
      continue;
    }

    // ATX Headings (# .. ######, with or without space for ##+)
    const heading = matchHeading(trimmed);
    if (heading) {
      flushParagraph(paragraphBuf);
      blocks.push({ kind: "heading", level: heading.level, text: heading.text });
      i += 1;
      continue;
    }

    // Blockquote
    if (trimmed.startsWith("> ")) {
      flushParagraph(paragraphBuf);
      const quoteLines: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("> ")) {
        quoteLines.push(lines[i].trim().slice(2));
        i += 1;
      }
      blocks.push({ kind: "blockquote", text: quoteLines.join(" ").trim() });
      continue;
    }

    // Unordered list
    if (/^[-*]\s+/.test(trimmed)) {
      flushParagraph(paragraphBuf);
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^[-*]\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ul", items });
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(trimmed)) {
      flushParagraph(paragraphBuf);
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^\d+\.\s+/, ""));
        i += 1;
      }
      blocks.push({ kind: "ol", items });
      continue;
    }

    paragraphBuf.push(trimmed);
    i += 1;
  }

  flushParagraph(paragraphBuf);
  return blocks;
}

/**
 * Tokenizes inline markdown and LaTeX math ($$...$$, $...$, \(...\), \[...\]) safely.
 */
export function parseInlineSegments(
  text: string,
  allowEmphasis = true,
): InlineSegment[] {
  const segments: InlineSegment[] = [];
  const pattern = allowEmphasis
    ? /(\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|`([^`]+)`|\$([^\s$][^$\n]*?[^\s$]|[^\s$])\$|\*\*([^*]+)\*\*|\*([^*\n]+)\*)/g
    : /(\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|`([^`]+)`|\$([^\s$][^$\n]*?[^\s$]|[^\s$])\$)/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ kind: "text", text: text.slice(lastIndex, match.index) });
    }

    if (match[2] !== undefined) {
      segments.push({ kind: "math_inline", latex: match[2].trim() });
    } else if (match[3] !== undefined) {
      segments.push({ kind: "math_inline", latex: match[3].trim() });
    } else if (match[4] !== undefined) {
      segments.push({ kind: "math_inline", latex: match[4].trim() });
    } else if (match[5] !== undefined) {
      segments.push({ kind: "code", text: match[5] });
    } else if (match[6] !== undefined) {
      segments.push({ kind: "math_inline", latex: match[6].trim() });
    } else if (allowEmphasis && match[7] !== undefined) {
      segments.push({ kind: "bold", text: match[7] });
    } else if (allowEmphasis && match[8] !== undefined) {
      segments.push({ kind: "italic", text: match[8] });
    }

    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    segments.push({ kind: "text", text: text.slice(lastIndex) });
  }

  return segments;
}
