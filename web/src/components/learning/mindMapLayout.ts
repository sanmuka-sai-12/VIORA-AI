import type { MindMapGraph, MindMapNode } from "../../api/client";

export interface PositionedMindMapNode {
  node: MindMapNode;
  x: number;
  y: number;
  width: number;
  height: number;
  hasChildren: boolean;
  isCollapsed: boolean;
  hiddenDescendantCount: number;
}

export interface PositionedMindMapEdge {
  id: string;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  relation: string | null;
}

export interface MindMapLayoutResult {
  nodes: PositionedMindMapNode[];
  edges: PositionedMindMapEdge[];
  width: number;
  height: number;
}

const NODE_WIDTH = 204;
const NODE_HEIGHT = 56;
const LEVEL_X_GAP = 264;
const LEAF_Y_GAP = 84;
const PADDING_X = 48;
const PADDING_Y = 48;

export function buildMindMapLayout(
  graph: MindMapGraph,
  collapsedIds: ReadonlySet<string>,
): MindMapLayoutResult {
  const byId = new Map<string, MindMapNode>();
  const childrenMap = new Map<string, MindMapNode[]>();

  for (const node of graph.nodes) {
    byId.set(node.node_id, node);
    if (node.parent_id) {
      const list = childrenMap.get(node.parent_id) ?? [];
      list.push(node);
      childrenMap.set(node.parent_id, list);
    }
  }

  const root = byId.get(graph.root_id) ?? graph.nodes[0];
  if (!root) {
    return { nodes: [], edges: [], width: 400, height: 240 };
  }

  const countDescendants = (nodeId: string): number => {
    const kids = childrenMap.get(nodeId) ?? [];
    let total = kids.length;
    for (const k of kids) {
      total += countDescendants(k.node_id);
    }
    return total;
  };

  let leafCounter = 0;
  const yCenterById = new Map<string, number>();
  const visibleOrder: MindMapNode[] = [];

  const assignPositions = (node: MindMapNode, visited: Set<string>): number => {
    if (visited.has(node.node_id)) {
      return leafCounter * LEAF_Y_GAP;
    }
    visited.add(node.node_id);
    visibleOrder.push(node);

    const children = childrenMap.get(node.node_id) ?? [];
    const collapsed = collapsedIds.has(node.node_id);

    if (children.length === 0 || collapsed) {
      const y = leafCounter * LEAF_Y_GAP;
      leafCounter += 1;
      yCenterById.set(node.node_id, y);
      return y;
    }

    const childYs = children.map((child) => assignPositions(child, visited));
    const minY = Math.min(...childYs);
    const maxY = Math.max(...childYs);
    const centerY = (minY + maxY) / 2;
    yCenterById.set(node.node_id, centerY);
    return centerY;
  };

  assignPositions(root, new Set());

  let maxRight = 0;
  let maxBottom = 0;
  const positionedById = new Map<string, PositionedMindMapNode>();
  const positionedNodes: PositionedMindMapNode[] = [];

  for (const node of visibleOrder) {
    const x = PADDING_X + node.depth * LEVEL_X_GAP;
    const centerY = yCenterById.get(node.node_id) ?? 0;
    const y = PADDING_Y + centerY;
    const kids = childrenMap.get(node.node_id) ?? [];
    const isCollapsed = collapsedIds.has(node.node_id);

    const placed: PositionedMindMapNode = {
      node,
      x,
      y,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      hasChildren: kids.length > 0,
      isCollapsed,
      hiddenDescendantCount: isCollapsed ? countDescendants(node.node_id) : 0,
    };
    positionedById.set(node.node_id, placed);
    positionedNodes.push(placed);
    maxRight = Math.max(maxRight, x + NODE_WIDTH);
    maxBottom = Math.max(maxBottom, y + NODE_HEIGHT);
  }

  const edges: PositionedMindMapEdge[] = [];
  for (const placed of positionedNodes) {
    const parentId = placed.node.parent_id;
    if (!parentId) continue;
    const parent = positionedById.get(parentId);
    if (!parent) continue;
    edges.push({
      id: `${parent.node.node_id}->${placed.node.node_id}`,
      fromX: parent.x + parent.width,
      fromY: parent.y + parent.height / 2,
      toX: placed.x,
      toY: placed.y + placed.height / 2,
      relation: placed.node.relation ?? null,
    });
  }

  return {
    nodes: positionedNodes,
    edges,
    width: Math.max(520, maxRight + PADDING_X),
    height: Math.max(260, maxBottom + PADDING_Y),
  };
}

export function getNodeBreadcrumb(
  graph: MindMapGraph,
  nodeId: string,
): MindMapNode[] {
  const byId = new Map<string, MindMapNode>();
  for (const n of graph.nodes) {
    byId.set(n.node_id, n);
  }
  const chain: MindMapNode[] = [];
  const seen = new Set<string>();
  let cur = byId.get(nodeId);
  while (cur && !seen.has(cur.node_id)) {
    seen.add(cur.node_id);
    chain.push(cur);
    cur = cur.parent_id ? byId.get(cur.parent_id) : undefined;
  }
  return chain.reverse();
}
