# FR-002 — Animated video generation

**Priority:** P0 | **Design status:** selected for build | **Implementation status:** not started

Input is an approved `LessonOutlineVersion`. Output is a set of versioned visual scenes and a renderable lesson. A scene should make an idea easier to understand: show force changing acceleration, the order of a queue, or movement through a process. Text flying onto the screen is not sufficient.

## Chosen approach

Use **deterministic templates first**, with an architecture that can add approved illustrations later. A model proposes a scene plan in JSON; the renderer maps its allowed commands onto tested components. Direct text-to-video generation is not part of P0.

| Approach | Trade-off | Decision |
|---|---|---|
| Direct generative-video model | Visually expressive but difficult to control units, labels, continuity and timing | Defer |
| Programmatic scenes only | Precise and repeatable but limited to available templates | Build first |
| Programmatic scenes plus approved assets | Supports wider subjects without losing the controlled render path | Long-term design |

**Initial engine:** Remotion 4.0.545 with React. Its license must be checked for the real team and usage before adoption; the scene JSON must be independent of that engine. If needed, SVG/Canvas plus FFmpeg can render the same constrained scene plan. Manim is a possible later mathematics adapter, not a P0 dependency.

## Scene specification

The scene model needs a version, parent outline version, order, objective/concept IDs, evidence refs, visual intent, objects, actions, permitted style tokens, narration text or script reference, timing anchors and asset refs.

Example — an *illustrative contract*, not the final JSON Schema:

```json
{
  "schema_version": "draft-1",
  "outline_version_id": "outline-v4",
  "scene_id": "force-01",
  "order": 1,
  "objective_ids": ["obj-2"],
  "concept_ids": ["force", "acceleration"],
  "evidence_refs": ["sec-2-p12"],
  "template": "vector_force",
  "script": "For the same mass, more force means more acceleration.",
  "objects": [
    {"id": "cart", "kind": "rectangle", "label": "cart"},
    {"id": "force", "kind": "vector_arrow", "label": "F"}
  ],
  "actions": [
    {"kind": "appear", "target": "cart", "anchor": "start"},
    {"kind": "increase_vector", "target": "force", "anchor": "more_force"}
  ],
  "timing_anchors": ["start", "more_force", "end"],
  "asset_refs": []
}
```

The actual schema will also specify coordinate space, units, allowed value ranges, z-order, fonts, easing, duration bounds and the renderer capability version. Reject unknown commands rather than trying to guess what the model meant.

## First scene catalog

| Template | Educational use | Required primitives |
|---|---|---|
| `vector_force` | Force, velocity, direction | Arrows, vectors, labels, moving object |
| `process_flow` | Water cycle, algorithms | Nodes, links, reveal and state changes |
| `parts_of_whole` | Fractions, cell parts | Regions, highlights, labels |
| `plot_relationship` | Trends, quantities, dependent variables | Axes, units, points, lines and animation |
| `labeled_diagram` | Structures, apparatus | Approved SVG/raster, callouts, highlights |

Keep the catalog small until at least two unrelated topic families pass review. Each scene declares what it is teaching; the UI can warn when no useful visual demonstration is present.

## Generation and timing

1. Confirm authorization and exact outline approval.
2. Map each learning objective to one or more scenes with a short explanation and named visual action anchors.
3. Validate scientific constraints available from the source: signs, units, ordered steps and the meaning of visual changes. Flag anything the rules cannot check.
4. Send the script and semantic anchors to FR-003. Synthesize audio before fixing final timings.
5. Calculate scene duration and animation offsets from actual audio timestamps, reading time and intentional pauses.
6. Render scenes individually, probe the media, check text overflow/contrast/blank frames and combine valid segments.
7. Store an immutable scene spec, media manifest, segment hashes and final timeline. Record which parent versions produced each result.
8. Let the publication gate decide whether the full lesson can be released.

A script edit requires new timing and probably a new render; changing one scene should not force the entire unaffected lesson to regenerate. Rendering and publication are durable jobs, but a completed render does not imply teacher approval.

## Safety and provenance

- Never run model-produced JS/Python, shell commands, raw HTML or script-capable SVG.
- Enforce limits on scene count, entities, resolution, CPU/memory, run time and allowed assets. Set numeric limits after benchmarks.
- Fetch media only from controlled storage. Reject unknown external URLs, active SVG scripts and unsupported fonts.
- Record asset origin, license/usage rights and source/version hashes.
- Check visual legibility and offer a reduced-motion approach later. Do not add intense flashing effects.
- Treat educational correctness separately from technical schema correctness; a valid vector arrow may still point in the wrong direction.

## Acceptance tests

1. A force/motion lesson contains an actual change demonstration with concept and objective references.
2. A second topic family produces a different useful visual style from the same scene system.
3. Unknown commands and remote asset URLs are rejected before rendering; no arbitrary code runs.
4. Rendering the same versioned spec and assets in the same environment produces equivalent frames within agreed tolerance.
5. A wrong unit or intentionally reversed vector is rejected by a domain rule or clearly flagged for teacher review.
6. Valid scenes yield playable media with known duration, resolution and timing anchors.
7. A revoked outline or denied resource cannot be rendered or published.
8. Retries, worker crashes and duplicate requests do not create duplicate published media.
9. Source and image rights are recorded; corrections create new versions without overwriting old ones.

## Decisions left for implementation

Final scene JSON Schema; renderer licensing; first templates; constraints and evaluation fixtures; media output format; exact time/size budget; teacher preview design; sandboxing; media storage beyond local development.
