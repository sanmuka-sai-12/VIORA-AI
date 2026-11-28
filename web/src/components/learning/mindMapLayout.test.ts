import test from "node:test";
import assert from "node:assert/strict";
import { buildMindMapLayout, getNodeBreadcrumb } from "./mindMapLayout.ts";
import type { MindMapGraph } from "../../api/client";

const SAMPLE_GRAPH: MindMapGraph = {
  title: "Derivatives",
  root_id: "n1",
  nodes: [
    {
      node_id: "n1",
      parent_id: null,
      depth: 0,
      label: "Derivatives",
      relation: null,
      explanation: "Measure instantaneous rate of change.",
      key_formula: "f'(x) = \\lim_{h \\to 0} \\frac{f(x+h)-f(x)}{h}",
    },
    {
      node_id: "n2",
      parent_id: "n1",
      depth: 1,
      label: "Power Rule",
      relation: "uses",
      explanation: "Differentiate x^n cleanly.",
      key_formula: null,
    },
    {
      node_id: "n3",
      parent_id: "n1",
      depth: 1,
      label: "Chain Rule",
      relation: "handles",
      explanation: "Differentiate composite functions.",
      key_formula: null,
    },
    {
      node_id: "n4",
      parent_id: "n3",
      depth: 2,
      label: "Inner Derivative",
      relation: "multiplies by",
      explanation: "Multiply outer derivative by g'(x).",
      key_formula: null,
    },
  ],
};

test("buildMindMapLayout computes non-overlapping coordinates and hides collapsed descendants", () => {
  const expanded = buildMindMapLayout(SAMPLE_GRAPH, new Set());
  assert.equal(expanded.nodes.length, 4);
  assert.equal(expanded.edges.length, 3);

  const collapsed = buildMindMapLayout(SAMPLE_GRAPH, new Set(["n3"]));
  assert.equal(collapsed.nodes.length, 3);
  const n3 = collapsed.nodes.find((p) => p.node.node_id === "n3");
  assert.equal(n3?.isCollapsed, true);
  assert.equal(n3?.hiddenDescendantCount, 1);

  assert.deepEqual(
    getNodeBreadcrumb(SAMPLE_GRAPH, "n4").map((n) => n.label),
    ["Derivatives", "Chain Rule", "Inner Derivative"],
  );
});
