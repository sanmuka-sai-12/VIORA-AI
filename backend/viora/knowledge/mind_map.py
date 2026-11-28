"""Bounded structured mind-map prompting, tree validation, and outline export."""

from __future__ import annotations

from ..security import AccessError
from .prompts import sanitize_untrusted_text
from .schemas import MindMapGraph, MindMapNode, RawMindMapPayload

MIND_MAP_SYSTEM_PROMPT = (
    "You are VIORA, an academic tutor generating a compact, structured concept mind map in JSON.\n"
    "Produce 7 to 13 concise nodes forming a clear hierarchy:\n"
    "- Exactly ONE root node with parent_id = null.\n"
    "- 2 to 4 main branches (depth 1) and helpful sub-concepts (depth 2-3).\n"
    "- Keep each label short (2-5 words), relation concise (1-3 words), and explanation clear (1-2 sentences, <= 180 chars).\n"
    "- Include key_formula only when mathematically relevant (clean LaTeX without outer $$).\n"
    "- Do NOT include executable code unless the topic is explicitly programming.\n"
    "- Ground concepts in the provided topic, conversation, and study material excerpts."
)


def _clean_text(value: str | None, *, max_len: int) -> str:
    if not value:
        return ""
    sanitized, _ = sanitize_untrusted_text(" ".join(value.split()).strip())
    if len(sanitized) <= max_len:
        return sanitized
    return sanitized[: max_len - 3].rstrip() + "..."


def normalize_mind_map_payload(raw: RawMindMapPayload) -> MindMapGraph:
    """Validate, repair dangling/cyclic parent links, and bound depths to 0..4."""
    dedup_nodes: list[tuple[str, str | None, str, str | None, str, str | None]] = []
    seen_ids: set[str] = set()

    for idx, node in enumerate(raw.nodes[:16], start=1):
        nid = _clean_text(node.id, max_len=32) or f"n{idx}"
        if nid in seen_ids:
            nid = f"n{idx}"
        seen_ids.add(nid)

        label = _clean_text(node.label, max_len=80)
        explanation = _clean_text(node.explanation, max_len=280)
        if not label or not explanation:
            continue
        parent = _clean_text(node.parent_id, max_len=32) or None
        relation = _clean_text(node.relation, max_len=40) or None
        formula = _clean_text(node.key_formula, max_len=120) or None
        dedup_nodes.append((nid, parent, label, relation, explanation, formula))

    if len(dedup_nodes) < 2:
        raise AccessError(
            422,
            "mind_map_incomplete",
            "Could not build a complete mind map from this output. Please narrow the topic or try regenerating.",
        )

    valid_ids = {item[0] for item in dedup_nodes}
    # Choose the first rootless node (or first node overall) as canonical root.
    root_id = next((item[0] for item in dedup_nodes if item[1] is None), dedup_nodes[0][0])

    parent_map: dict[str, str | None] = {}
    for nid, parent, *_ in dedup_nodes:
        if nid == root_id:
            parent_map[nid] = None
        elif parent is None or parent not in valid_ids or parent == nid:
            parent_map[nid] = root_id
        else:
            parent_map[nid] = parent

    # Break any cycles cleanly by re-attaching back-edges to root_id.
    depths: dict[str, int] = {root_id: 0}

    def resolve_depth(node_id: str) -> int:
        if node_id in depths:
            return depths[node_id]
        trail: list[str] = []
        visiting: set[str] = set()
        cur: str | None = node_id
        while cur is not None and cur not in depths:
            if cur in visiting:
                # Cycle detected: break at `cur` by attaching to root_id.
                parent_map[cur] = root_id
                break
            visiting.add(cur)
            trail.append(cur)
            cur = parent_map.get(cur)

        base_depth = depths.get(cur, 0) if cur is not None else 0
        for offset, item in enumerate(reversed(trail), start=1):
            d = base_depth + offset
            if d > 4:
                # Clamp over-deep chains onto root_id cleanly.
                parent_map[item] = root_id
                depths[item] = 1
            else:
                depths[item] = d
        return depths[node_id]

    for nid, *_ in dedup_nodes:
        resolve_depth(nid)

    canonical_nodes: list[MindMapNode] = []
    for nid, _, label, relation, explanation, formula in dedup_nodes:
        resolved_parent = parent_map[nid]
        canonical_nodes.append(
            MindMapNode(
                node_id=nid,
                parent_id=resolved_parent,
                depth=depths[nid],
                label=label,
                relation=None if nid == root_id else relation,
                explanation=explanation,
                key_formula=formula,
            )
        )

    title = _clean_text(raw.title, max_len=120) or canonical_nodes[0].label
    return MindMapGraph(title=title, root_id=root_id, nodes=tuple(canonical_nodes))


def mind_map_to_markdown(graph: MindMapGraph) -> str:
    """Render a clean hierarchical markdown outline for copy/fallback compatibility."""
    children: dict[str | None, list[MindMapNode]] = {}
    for node in graph.nodes:
        children.setdefault(node.parent_id, []).append(node)

    lines: list[str] = []

    def walk(node: MindMapNode, indent: int) -> None:
        prefix = "  " * indent
        rel = f" *({node.relation})*" if node.relation else ""
        formula = f" — `${node.key_formula}$`" if node.key_formula else ""
        lines.append(f"{prefix}- **{node.label}**{rel}: {node.explanation}{formula}")
        for child in children.get(node.node_id, ()):
            walk(child, indent + 1)

    for root in children.get(None, ()):
        walk(root, 0)
    return "\n".join(lines)
