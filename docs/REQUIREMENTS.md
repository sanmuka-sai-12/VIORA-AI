# Requirements

**VIORA-AI | Baseline dated 23 November 2026**

This is the main requirements register. The feature split follows the *Intelligent Visual Learning Platform* problem statement: five features for the online round and six for the offline round. Five additional requirements describe VIORA-AI's later institution platform.

**Status:** FR-001 is approved. FR-002–016 have been designed as the working plan for development; their implementation is not complete. The stack is selected for the first build, with installation and integration checks still pending.

## Product rules

1. Every generated lesson needs a clear learning objective. At least one scene should demonstrate a relationship, process or change; a moving slideshow alone is not enough.
2. Teacher approval is required for the exact outline version used to start video generation. A successful AI job is not approval.
3. Generated claims, diagrams and questions must keep source references where source material exists. Topic-only prompts are marked as inferred rather than pretending to cite a textbook.
4. Model output is untrusted data. The renderer accepts a defined scene schema, not arbitrary Python, JavaScript, shell commands or raw SVG scripts.
5. Publishing checks authorization, validation and version consistency. A newer edit cannot inherit an older approval.
6. Long tasks have durable status, bounded retries and recoverable checkpoints. Replayed jobs must not create duplicate published artifacts.

## P0 — Online-round features

| ID | Requirement | Release test |
|---|---|---|
| **FR-001** | Accept lesson notes, topic descriptions, objectives and text PDFs; identify concepts, prerequisites, objectives, teaching order and visual opportunities. | Outline validates against the schema; evidence and warnings are visible; teacher approves its exact version. |
| **FR-002** | Plan scenes, diagrams and animations from the approved outline; render a playable lesson with controlled scene instructions. | One tested concept has a meaningful visual explanation, a playable scene, and recorded scene IDs and timing anchors. |
| **FR-003** | Generate speech, align the script with scene actions, and provide captions, labels and keyword timing. | Captions and significant on-screen actions follow the actual narration on the test lesson. |
| **FR-004** | Produce a chapter summary, formulas when relevant, learning outcomes, key points and revision notes. | Summary refers to the published lesson and approved concepts, with unsupported claims flagged. |
| **FR-005** | Generate MCQs, concept checks and short quizzes, grade answers and return explanations. | Answer keys are checked before publication; scoring uses stored correct answers and never exposes the key before submission. |

FR-001 establishes the `LessonOutlineVersion` contract. FR-002 creates scene specifications and visual segments. FR-003 finalizes narration and timing. FR-004 and FR-005 consume the approved concept set and the actual published lesson version.

## P1 — Offline-round features

| ID | Requirement | Delivery approach | Release test |
|---|---|---|---|
| **FR-006** | Primary, secondary and undergraduate lesson versions | One concept model with level-specific language, examples and scene complexity | Same core fact explained at three levels without contradiction |
| **FR-007** | Multilingual narration and subtitles | Localized scripts, glossary, TTS and a separate timed track per language | Two supported languages work; switching languages does not break timing |
| **FR-008** | Accessibility | Captions, transcript, keyboard control, high contrast and audio description; sign-language overlay through reviewed media | Accessible playback checks on a representative lesson; do not claim automatic sign-language translation |
| **FR-009** | Interactive checkpoints | Versioned pause cues linked to questions, hints and resume positions | Player pauses at the correct point and records one logical answer event |
| **FR-010** | Offline learning package | Export a media/summary/quiz manifest with checksums and a local progress journal | Lesson works without network; reconnect syncs without double counting |
| **FR-011** | Teacher editing | Safe scene/template editing, script changes, asset replacement, versioned approval | Editing one scene regenerates affected outputs and leaves other approved artifacts intact |

The offline list follows the supplied brief. Whether each feature must be fully implemented during the event depends on the instructions given for that round.

## P2 — VIORA-AI institutional features

These five features come from the V4 product architecture, not the hackathon problem statement.

| ID | Requirement | Main check |
|---|---|---|
| **FR-012** | Institutions, academic terms, classes, subjects, enrollment and scoped roles | A teacher can access assigned classes, not an unrelated institution |
| **FR-013** | Evidence ledger, concept mastery and learning recommendations | Every mastery estimate can be traced back to versioned student evidence and is marked uncertain when data is sparse |
| **FR-014** | Teacher intervention and remediation assignments | Only an authorized educator can assign and review an intervention |
| **FR-015** | Governed knowledge sharing with permission and revocation | Revoking access prevents new protected reads and publication; lineage remains recorded |
| **FR-016** | Student, class and organization analytics | Metrics reconcile with source events; cohort privacy and scoped access are enforced |

## Technical stack — initial install baseline

These are **selected tools for the first development sessions**. They are not simply a list of alternatives. Exact direct dependency pins are supplied in `backend/requirements.txt` and `web/package.json`.

| Layer | Chosen tool | Version / install target | Reason |
|---|---|---|---|
| Runtime — backend | Python | **3.13.16** | Stable supported runtime with broad binary-wheel support |
| API framework | FastAPI | **0.165.0** | Typed asynchronous HTTP API, validation/OpenAPI |
| Data validation | Pydantic | **2.14.4** | Strict model and scene-contract validation |
| Database | PostgreSQL | **17.12** | Sources, versions, jobs, permissions and results |
| ORM / migrations | SQLAlchemy / Alembic | **2.1.9 / 1.20.1** | Relational mapping and schema changes |
| PDF extraction | pdfplumber / pypdf | **0.11.11 / 7.1.1** | Page-level positions and baseline PDF handling |
| API / AI client | OpenAI Python SDK | **3.57.0** | Initial provider adapter; model and speech provider are configurable |
| Runtime — web / renderer | Node.js LTS | **24.22.3** | Shared frontend and video-rendering runtime |
| Web app | React / Vite / TypeScript | **19.3.1 / 8.3.8 / 7.0.2** | Lean UI without an additional server framework |
| Programmatic video | Remotion | **4.0.545** (all Remotion packages aligned) | Frame-accurate React scene templates and rendering |
| Media tools | FFmpeg | **8.1 branch** (OS-specific binary) | Media inspection, fallback encoding and synchronization checks |
| Local services | Docker Compose + PostgreSQL image | `postgres:17.12` | Repeatable database setup |

See [TECH-STACK.md](TECH-STACK.md) for the **full install commands, complete Python/npm package pins, compatibility checks, license constraints, options not installed by default and version sources**. Do not add alternative libraries to the manifests just because they are under consideration.

### Why these and not a larger stack?

- **FastAPI instead of Django:** the early work is mostly APIs, workers and AI processing rather than Django-admin-driven content management. Django remains viable if institution administration becomes the main workload.
- **Vite/React instead of Next.js:** the backend already exposes the API. We do not need server-side React rendering for the teacher/student portal at this stage.
- **Remotion as the first video engine, not a generative-video model:** reproducible math/diagram scenes and reliable timing matter more than cinematic shots. Manim can later handle specialized mathematical scenes, but is not required for the first vertical slice.
- **PostgreSQL-backed jobs before Redis/Celery:** durable polling/claiming with leases is adequate for one or a few workers. Add a broker only if measured workload demands it.
- **pdfplumber before PyMuPDF:** positional text extraction meets the approved PDF scope, with a less complicated starting license position. OCR is intentionally deferred.

## Nonfunctional requirements

| Area | Requirement | How we check |
|---|---|---|
| Security | Tenant membership plus resource/action permission checked on API, worker, retrieval and publishing paths | Cross-tenant and revoked-access tests |
| Privacy | Avoid placing raw student responses, private chapter text or credentials in logs | Log inspection, secret scanning |
| Reliability | Jobs survive process restart; retries are bounded; artifacts are immutable | Kill/restart worker; duplicate request and stale-version tests |
| Quality | Grounding, educational correctness, diagrams and quiz keys reviewed independently of schema validation | Golden lessons, deliberate error fixtures, teacher review |
| Media | Video plays in target browser; narration, captions and keyword cues align | Media probe, playback and timing checks |
| Accessibility | Keyboard navigation, contrast, captions and readable controls | Manual keyboard check and automated accessibility tests |
| Cost | Per-request tokens, generated audio and rendering minutes are logged and capped | Budget tests and limit behavior |
| Performance | Processing time, queue delay and render cost are measured | Benchmark before numerical SLAs are set |
| Portability | Environment variables and manifests work on the documented toolchain | Clean setup on Windows plus Linux CI |

We are **not** claiming specific latency, accuracy percentages or WCAG conformance before tests exist. Set measurable thresholds using a representative lesson corpus in M0/M1.

## Publication check

Before a generated lesson is visible to students, check all of the following:

1. Current access to the source and destination audience.
2. Exact teacher-approved outline version, with no blocking grounding issue.
3. Valid scene and script versions; no unresolved render errors.
4. Narration/caption timeline matches the media version.
5. Summary and question versions refer to the current lesson; answer keys verified.
6. No stale dependency caused by a subsequent edit or revocation.
7. Publication and review actions recorded in the audit trail.

## Feature documents

- [FR-001](FR-001-CONTENT-UNDERSTANDING.md) — approved requirement and processing lifecycle.
- [FR-002](FR-002-ANIMATED-VIDEO.md) — scenes, templates, rendering and visual checks.
- [FR-003–005](FR-003-TO-005.md) — audio alignment, summaries and assessments.
- [FR-006–011](FR-006-TO-011-OFFLINE-ROUND.md) — offline-round capabilities.
- [FR-012–016](FR-012-TO-016-PLATFORM.md) — institution, mastery, intervention, sharing and analytics.
- [Architecture](ARCHITECTURE-V4-FULL.md) and [build plan](DELIVERY-PLAN.md).
