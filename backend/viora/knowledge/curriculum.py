"""Deterministic prerequisite DAG analysis, cycle breaking, and structured lesson outlines."""

from __future__ import annotations

import heapq
from collections import deque

from .schemas import (
    CURRICULUM_ALGORITHM_VERSION,
    ConceptCandidate,
    ConceptRelationshipCandidate,
    ExtractedKnowledgeGraph,
    GroundingClassification,
    LessonModule,
    LessonOutlineStep,
    PrerequisiteAnalysis,
    PrerequisiteCycle,
    StructuredLessonOutline,
    TeachingOrderStep,
)

_GROUNDING_STRENGTH: dict[GroundingClassification, int] = {
    "source_supported": 5,
    "source_synthesized": 4,
    "inferred": 3,
    "learning_request": 2,
    "uncertain": 1,
    "unsupported": 0,
}


def _find_sccs(
    node_ids: list[str], adj: dict[str, list[str]]
) -> list[list[str]]:
    """Tarjan strongly connected components in deterministic node order."""
    index = 0
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj[v]:
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.remove(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for nid in node_ids:
        if nid not in indices:
            strongconnect(nid)
    return sccs


def analyze_prerequisites_and_order(
    knowledge: ExtractedKnowledgeGraph,
) -> PrerequisiteAnalysis:
    """Detect prerequisite cycles deterministically and compute pedagogical teaching order."""
    concepts = list(knowledge.concepts)
    by_id: dict[str, ConceptCandidate] = {c.concept_id: c for c in concepts}
    node_ids = [c.concept_id for c in concepts]
    node_rank = {cid: idx for idx, cid in enumerate(node_ids)}

    edges: dict[str, ConceptRelationshipCandidate] = {}
    for rel in knowledge.relationships:
        if (
            rel.relation_type == "prerequisite_of"
            and rel.source_concept_id in by_id
            and rel.target_concept_id in by_id
        ):
            edges[rel.relationship_id] = rel

    active_edge_ids = set(edges.keys())
    cycles: list[PrerequisiteCycle] = []
    blocked_nodes: set[str] = set()

    # Handle self-loops first cleanly
    for eid in sorted(active_edge_ids):
        rel = edges[eid]
        if rel.source_concept_id == rel.target_concept_id:
            active_edge_ids.remove(eid)
            blocked_nodes.add(rel.source_concept_id)
            cycles.append(
                PrerequisiteCycle(
                    cycle_id=f"cyc-{len(cycles) + 1}",
                    concept_ids=(rel.source_concept_id,),
                    concept_names=(by_id[rel.source_concept_id].name,),
                    broken_edge_id=eid,
                    resolution_note=f"Removed self-referential prerequisite on '{by_id[rel.source_concept_id].name}'.",
                )
            )

    # Build adjacency once and run Tarjan SCC once on the full graph; iteratively resolve only cyclic SCC subgraphs.
    adj: dict[str, list[str]] = {cid: [] for cid in node_ids}
    outgoing_edge_ids: dict[str, list[str]] = {cid: [] for cid in node_ids}
    for eid in active_edge_ids:
        rel = edges[eid]
        adj[rel.source_concept_id].append(rel.target_concept_id)
        outgoing_edge_ids[rel.source_concept_id].append(eid)
    for adj_list in adj.values():
        adj_list.sort(key=lambda x: node_rank[x])

    pending_sccs: deque[list[str]] = deque(
        c for c in _find_sccs(node_ids, adj) if len(c) > 1
    )
    while pending_sccs:
        scc = sorted(pending_sccs.popleft(), key=lambda x: node_rank[x])
        scc_set = set(scc)
        blocked_nodes.update(scc_set)
        in_scc_edges = [
            edges[eid]
            for cid in scc
            for eid in outgoing_edge_ids[cid]
            if eid in active_edge_ids
            and edges[eid].target_concept_id in scc_set
        ]
        if not in_scc_edges:
            continue
        # Prefer breaking the weakest grounded, unverified, latest edge in the SCC
        weakest = min(
            in_scc_edges,
            key=lambda r: (
                _GROUNDING_STRENGTH[r.grounding],
                len(r.evidence_bindings),
                -node_rank[r.source_concept_id],
                -node_rank[r.target_concept_id],
            ),
        )
        active_edge_ids.remove(weakest.relationship_id)
        cycles.append(
            PrerequisiteCycle(
                cycle_id=f"cyc-{len(cycles) + 1}",
                concept_ids=tuple(scc),
                concept_names=tuple(by_id[cid].name for cid in scc),
                broken_edge_id=weakest.relationship_id,
                resolution_note=(
                    f"Broke prerequisite cycle edge '{by_id[weakest.source_concept_id].name}' -> "
                    f"'{by_id[weakest.target_concept_id].name}' for ordered instruction."
                ),
            )
        )
        sub_adj: dict[str, list[str]] = {cid: [] for cid in scc}
        for rel in in_scc_edges:
            if rel.relationship_id != weakest.relationship_id:
                sub_adj[rel.source_concept_id].append(rel.target_concept_id)
        for adj_list in sub_adj.values():
            adj_list.sort(key=lambda x: node_rank[x])
        for sub_scc in reversed([c for c in _find_sccs(scc, sub_adj) if len(c) > 1]):
            pending_sccs.appendleft(sub_scc)

    # Kahn topological sort with stable document reading-order priority queue
    children: dict[str, list[str]] = {cid: [] for cid in node_ids}
    parents: dict[str, list[str]] = {cid: [] for cid in node_ids}
    indegree: dict[str, int] = {cid: 0 for cid in node_ids}
    for eid in sorted(active_edge_ids):
        rel = edges[eid]
        children[rel.source_concept_id].append(rel.target_concept_id)
        parents[rel.target_concept_id].append(rel.source_concept_id)
        indegree[rel.target_concept_id] += 1

    heap: list[tuple[int, str]] = [
        (node_rank[cid], cid) for cid in node_ids if indegree[cid] == 0
    ]
    heapq.heapify(heap)
    ordered_ids: list[str] = []
    depth_by_id: dict[str, int] = {}
    while heap:
        _, cid = heapq.heappop(heap)
        ordered_ids.append(cid)
        depth_by_id[cid] = max(
            (depth_by_id[p] + 1 for p in parents[cid]),
            default=0,
        )
        for nxt in children[cid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                heapq.heappush(heap, (node_rank[nxt], nxt))

    root_ids = tuple(cid for cid in ordered_ids if not parents[cid])
    steps = tuple(
        TeachingOrderStep(
            order_index=idx + 1,
            depth_level=depth_by_id[cid],
            concept_id=cid,
            concept_name=by_id[cid].name,
            prerequisite_concept_ids=tuple(
                sorted(parents[cid], key=lambda p: node_rank[p])
            ),
            blocked_by_cycle=cid in blocked_nodes,
            grounding=by_id[cid].grounding,
            evidence_ids=tuple(b.evidence_id for b in by_id[cid].evidence_bindings),
            needs_review=by_id[cid].needs_review or (cid in blocked_nodes),
        )
        for idx, cid in enumerate(ordered_ids)
    )
    return PrerequisiteAnalysis(
        algorithm_version=CURRICULUM_ALGORITHM_VERSION,
        total_concepts=len(concepts),
        total_prerequisite_edges=len(edges),
        has_cycles=len(cycles) > 0,
        cycles=tuple(cycles),
        root_concept_ids=root_ids,
        teaching_order=steps,
    )


def _aggregate_grounding(groundings: list[GroundingClassification]) -> GroundingClassification:
    if not groundings:
        return "uncertain"
    if all(g == "learning_request" for g in groundings):
        return "learning_request"
    return min(groundings, key=lambda g: _GROUNDING_STRENGTH[g])


def generate_lesson_outline(
    knowledge: ExtractedKnowledgeGraph,
) -> StructuredLessonOutline:
    """Build a structured, evidence-linked lesson outline ordered by prerequisites."""
    analysis = analyze_prerequisites_and_order(knowledge)
    by_id = {c.concept_id: c for c in knowledge.concepts}

    by_depth: dict[int, list[TeachingOrderStep]] = {}
    for step in analysis.teaching_order:
        by_depth.setdefault(step.depth_level, []).append(step)

    sorted_depths = sorted(by_depth.keys())
    modules: list[LessonModule] = []
    total_minutes = 0
    supported_steps = 0
    review_steps = 0

    for m_idx, depth in enumerate(sorted_depths):
        mod_steps: list[LessonOutlineStep] = []
        mod_objs: list[str] = []
        mod_ev_ids: list[str] = []
        mod_groundings: list[GroundingClassification] = []
        mod_minutes = 0

        for step in by_depth[depth]:
            c = by_id[step.concept_id]
            est_min = min(
                45,
                5 + 3 * len(c.definitions) + 4 * len(c.examples) + 3 * len(c.learning_objectives),
            )
            mod_minutes += est_min
            if c.grounding in {"source_supported", "source_synthesized"} and not step.needs_review:
                supported_steps += 1
            else:
                review_steps += 1
            mod_groundings.append(c.grounding)
            for lo in c.learning_objectives:
                if lo.statement not in mod_objs:
                    mod_objs.append(lo.statement)
            for b in c.evidence_bindings:
                if b.evidence_id not in mod_ev_ids:
                    mod_ev_ids.append(b.evidence_id)

            mod_steps.append(
                LessonOutlineStep(
                    step_index=step.order_index,
                    depth_level=depth,
                    concept_id=c.concept_id,
                    concept_name=c.name,
                    sense_label=c.sense_label,
                    canonical_summary=c.canonical_summary,
                    definitions=c.definitions,
                    examples=c.examples,
                    learning_objectives=c.learning_objectives,
                    prerequisite_concept_ids=step.prerequisite_concept_ids,
                    grounding=c.grounding,
                    evidence_bindings=c.evidence_bindings,
                    needs_review=step.needs_review,
                    estimated_minutes=est_min,
                )
            )

        total_minutes += mod_minutes
        if len(sorted_depths) == 1:
            mod_title = f"Module {m_idx + 1}: Core Concepts"
        elif m_idx == 0:
            mod_title = f"Module {m_idx + 1}: Foundations"
        elif m_idx == len(sorted_depths) - 1:
            mod_title = f"Module {m_idx + 1}: Synthesis & Application"
        else:
            mod_title = f"Module {m_idx + 1}: Building Concepts (Level {depth + 1})"

        modules.append(
            LessonModule(
                module_id=f"mod-{m_idx + 1}",
                sequence_index=m_idx + 1,
                depth_level=depth,
                title=mod_title,
                learning_objectives=tuple(mod_objs),
                steps=tuple(mod_steps),
                module_grounding=_aggregate_grounding(mod_groundings),
                evidence_ids=tuple(mod_ev_ids),
                estimated_minutes=mod_minutes,
            )
        )

    is_learning_req = knowledge.input_format == "topic_description"
    all_verified = (
        len(knowledge.concepts) > 0
        and not is_learning_req
        and all(len(c.evidence_bindings) > 0 for c in knowledge.concepts)
        and knowledge.validation.unsupported_count == 0
        and knowledge.validation.uncertain_count == 0
    )
    notes = list(knowledge.validation.validation_notes)
    if analysis.has_cycles:
        notes.append(
            f"Resolved {len(analysis.cycles)} prerequisite cycle(s) deterministically; teacher verification recommended."
        )

    return StructuredLessonOutline(
        outline_version=CURRICULUM_ALGORITHM_VERSION,
        source_id=knowledge.source_id,
        version_id=knowledge.version_id,
        derived_extraction_id=knowledge.derived_extraction_id,
        source_sha256=knowledge.source_sha256,
        input_format=knowledge.input_format,
        title=knowledge.title,
        learning_request_only=is_learning_req,
        all_evidence_verified=all_verified,
        requires_teacher_review=True,
        has_prerequisite_cycles=analysis.has_cycles,
        prerequisite_analysis=analysis,
        modules=tuple(modules),
        estimated_total_minutes=total_minutes,
        supported_steps_count=supported_steps,
        review_required_steps_count=review_steps,
        notes=tuple(notes),
    )
