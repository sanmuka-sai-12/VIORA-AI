# FR-001 — Content understanding

**Priority:** P0 | **Requirements status:** approved | **Implementation status:** not started

A teacher uploads lesson material or enters a topic. VIORA-AI identifies what should be taught, the relationships between ideas and the order in which the explanation should run. The result is a structured `LessonOutline`, not a finished video.

## Agreed design

| ID | Decision |
|---|---|
| D01 | Accept plain text, topic descriptions and text-based PDFs. Scanned PDFs, image OCR and audio/video input are outside the first build. |
| D02 | Work section by section, then consolidate ideas and teaching order across the document. |
| D03 | Produce versioned `LessonOutline` JSON with grounding classifications, evidence references and validation results. |
| D04 | Use layout-aware PDF extraction, preserve sections/page positions and split only oversized sections. Report incomplete extraction. |
| D05 | Run processing as durable background jobs with retries/checkpoints/idempotent effects. Require teacher approval of an exact outline version. |

## Input and output

**Inputs:** text notes, lesson objectives, a short topic prompt or a text PDF. Save the original file or text and its hash as an immutable `SourceVersion` with tenant and access metadata.

**Intermediate data:** ordered `SourceSection` records with headings, parent section, page numbers, passage positions and extraction warnings. Keep the educational section separate from the smaller chunks sent to a model.

**Output:** one `LessonOutlineVersion` containing at least:

- Schema version, source version, title, subject and target level if known.
- Concepts with stable IDs, definitions and evidence references.
- Learning objectives and explicit relationships/prerequisite candidates.
- Teaching steps in order, concept IDs, explanation goals and visual suggestions.
- Validation results, warnings and each claim's grounding status.

Use `source_supported` for statements grounded in the uploaded material, `inferred` for plausible additions that need review, `unsupported` for claims that must not pass publication, and `unverifiable` when source extraction or evidence is inadequate. A bare topic prompt is itself a user-supplied request, not independent proof of factual content; label generated explanations appropriately.

## Processing flow

1. Check active membership and permission to create content in the selected workspace.
2. Register the source and an idempotent job; return its ID immediately.
3. Extract text and available layout information. A corrupt, protected or image-only PDF returns an actionable failure; do not silently treat an empty extraction as successful.
4. Reconstruct sections, remove repeated headers where reliable, maintain reading order and flag suspicious tables, formulas and image-only passages.
5. Extract concepts and objectives from each section. Merge duplicates carefully and derive the global teaching sequence.
6. Validate schema, IDs, source references and semantic support. Schema validity does not prove scientific correctness.
7. Save an immutable draft. Display concepts, teaching order and warnings for teacher review.
8. Record `approved`, `needs_revision` or `rejected` against the exact version. An edit creates a new version and requires its own approval.

## Background job rules

Processing states: `queued`, `running`, `retry_wait`, `succeeded`, `failed`, `cancelled`. Track the active stage separately (`extracting`, `analyzing`, `synthesizing`, `validating`). Review states belong to the *artifact*, not the job.

A transient network/provider failure may retry with bounded exponential backoff. Unsupported input, denied access and persistent schema failure must terminate with an understandable error. Use persisted checkpoints and worker leases for crash recovery. Same tenant + operation + idempotency key + identical payload returns the existing job; a changed payload under the same key is a conflict. External model calls can execute more than once after a crash; make database effects idempotent rather than promising exactly-once AI execution.

Teacher approval never overrides critical source-grounding failure, missing permission or source revocation. FR-002 may consume only an approved, valid and currently accessible outline version.

## Acceptance tests

1. Plain text, topic prompts and ordinary text PDFs return a valid outline or a clear failure.
2. A two-section PDF keeps section order and correct page/passage references.
3. Oversized content is split for processing without losing section lineage.
4. A PDF with no extractable text fails with a warning that OCR is unsupported at this stage.
5. Duplicate concept names consolidate without mixing incompatible meanings.
6. Missing evidence and unsupported claims are identified; critical unresolved claims block publication.
7. Invalid JSON or dangling IDs are rejected; repair/retry attempts are bounded.
8. A worker restart does not permanently lose an accepted job.
9. Repeated submissions do not create duplicate logical artifacts; changed payload with reused key causes a conflict.
10. A different tenant cannot read the job, source, draft or approval decision.
11. Approval is tied to the exact outline version; editing invalidates old approval for the new version.
12. Unapproved or revoked outlines cannot start FR-002 processing.

## Details to settle during implementation

Exact JSON Schema, extraction quality rules, upload size/page/token limits, timeout and retry numbers, per-job costs, review UI fields, and the outstanding authorization policy in DEC-003. The initial PDF libraries are `pdfplumber` and `pypdf` as listed in [TECH-STACK.md](TECH-STACK.md); benchmark them before claiming extraction accuracy.
