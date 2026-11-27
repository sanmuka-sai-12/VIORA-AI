"""Build the student projection of a teacher-reviewed graph."""

from .schemas import ExtractedKnowledgeGraph


def reviewed_graph(
    graph: ExtractedKnowledgeGraph, rejected_ids: tuple[str, ...]
) -> ExtractedKnowledgeGraph:
    rejected = set(rejected_ids)
    removed_names = {
        name.strip().casefold()
        for concept in graph.concepts
        if concept.concept_id in rejected
        for name in (concept.name, concept.normalized_name)
    }
    concepts = tuple(
        concept.model_copy(update={
            "prerequisites": tuple(
                prerequisite for prerequisite in concept.prerequisites
                if prerequisite.prerequisite_name.strip().casefold() not in removed_names
            ),
        })
        for concept in graph.concepts
        if concept.concept_id not in rejected
    )
    retained = {concept.concept_id for concept in concepts}
    return graph.model_copy(update={
        "concepts": concepts,
        "relationships": tuple(
            edge for edge in graph.relationships
            if {edge.source_concept_id, edge.target_concept_id} <= retained
        ),
    })


def publication_complete(graph: ExtractedKnowledgeGraph) -> bool:
    """Reject explicit incompleteness while accepting legacy unknown telemetry."""
    return bool(graph.concepts) and graph.validation.required_fields_complete is not False
