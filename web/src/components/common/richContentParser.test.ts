import test from "node:test";
import assert from "node:assert/strict";
import {
  parseAcademicBlocks,
  parseInlineSegments,
} from "./richContentParser.ts";

test("parseAcademicBlocks extracts headings (including h4-h6 and malformed), math blocks, code fences, dividers, and lists cleanly", () => {
  const sample = [
    "# Chain Rule",
    "#### Subheading Level 4",
    "##Compact Heading##",
    "###",
    "Section Title",
    "---",
    "",
    "When differentiating $f(g(x))$, multiply outer and inner derivatives:",
    "",
    "$$",
    "\\frac{d}{dx} f(g(x)) = f'(g(x)) \\cdot g'(x)",
    "$$",
    "",
    "```python",
    "def f(x):",
    "    return (3*x + 1)**2",
    "```",
    "",
    "---",
    "",
    "- Step 1: Differentiate outer function",
    "- Step 2: Multiply by inner derivative",
  ].join("\n");

  const blocks = parseAcademicBlocks(sample);
  assert.deepEqual(
    blocks.map((b) => b.kind),
    [
      "heading",
      "heading",
      "heading",
      "heading",
      "paragraph",
      "math_block",
      "code",
      "divider",
      "ul",
    ],
  );
  if (blocks[1].kind === "heading") {
    assert.equal(blocks[1].level, 3);
    assert.equal(blocks[1].text, "Subheading Level 4");
  }
  if (blocks[2].kind === "heading") {
    assert.equal(blocks[2].text, "Compact Heading");
  }
  if (blocks[3].kind === "heading") {
    assert.equal(blocks[3].level, 2);
    assert.equal(blocks[3].text, "Section Title");
  }
  if (blocks[5].kind === "math_block") {
    assert.ok(blocks[5].latex.includes("f'(g(x))"));
  }
  if (blocks[6].kind === "code") {
    assert.equal(blocks[6].language, "python");
    assert.ok(blocks[6].code.includes("def f(x):"));
  }
});

test("parseInlineSegments extracts inline $...$, inline $$...$$, inline code, and bold text cleanly", () => {
  const segments = parseInlineSegments(
    "Binary search runs in $O(\\log n)$ or $$T(n) = T(n/2) + O(1)$$ using `mid = (lo + hi) // 2` for **sorted** arrays.",
  );
  assert.equal(
    segments.some((s) => s.kind === "math_inline" && s.latex === "O(\\log n)"),
    true,
  );
  assert.equal(
    segments.some(
      (s) => s.kind === "math_inline" && s.latex === "T(n) = T(n/2) + O(1)",
    ),
    true,
  );
  assert.equal(
    segments.some((s) => s.kind === "code" && s.text === "mid = (lo + hi) // 2"),
    true,
  );
  assert.equal(
    segments.some((s) => s.kind === "bold" && s.text === "sorted"),
    true,
  );
});
