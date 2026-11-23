# Decision log

This file records the choices that affect other modules. We keep requirements, design preferences and tested implementations separate so a later change does not get hidden in an older document.

**Status terms:**
- **Approved:** explicitly accepted for the requirements baseline.
- **Selected for build:** planned implementation; not yet proven by an integration test.
- **Open:** unresolved; the affected feature stays guarded or deferred.

## Product and architecture

| ID | Decision | Status | Reason |
|---|---|---|---|
| DEC-001 | Build an educational content-to-animation platform in phases | Approved | First get one useful lesson through the whole pipeline |
| DEC-002 | Support student, teacher, head/director and platform admin roles with scoped memberships | Approved | One account can have different responsibilities in different contexts |
| DEC-003 | Tenant membership, resource ownership, revocation and special access policy | Open | Needs an explicit authorization policy before multi-institution use |
| DEC-004 | Online-round core before offline-round and institutional extensions | Approved | Matches competition priorities |
| DEC-005 | Modular monolith with separate long-running workers | Selected for build | Keeps transactions and code ownership understandable |
| DEC-006 | PostgreSQL for authoritative records; protected media storage; derived indexes rebuildable | Selected for build | Reliable versions, relationships and audit history |
| DEC-007 | Database-backed durable jobs for first release; no Redis/Celery requirement | Selected for build | Fewer moving parts while worker volume is small |
| DEC-008 | Immutable source and generated artifact versions, linked by parent IDs | Selected for build | Prevents accidentally mixing outdated outputs |
| DEC-009 | Only validated scene data reaches the renderer | Selected for build | Model-generated code would be unsafe and difficult to reproduce |
| DEC-010 | Captions and keyboard playback start in the online prototype | Selected for build | Required for a usable narrated lesson |
| DEC-011 | Quiz answer keys stay on the server; offline quiz grading is practice-only | Selected for build | A downloadable package cannot protect a secret answer key |
| DEC-012 | Use a configurable AI provider interface; first SDK integration is OpenAI | Selected for build | Avoid coupling the rest of the system to one model or API shape |

## FR-001 — approved

| Decision | Requirement |
|---|---|
| FR-001-D01 | Plain text, topic descriptions and text-based PDFs; scanned PDFs/OCR deferred |
| FR-001-D02 | Extract section-level concepts first, then consolidate into a teaching sequence |
| FR-001-D03 | Versioned `LessonOutline` JSON with source references, grounding flags, validation and immutable versions |
| FR-001-D04 | Layout-aware extraction, section-first segmentation, bounded chunks and extraction warnings |
| FR-001-D05 | Durable asynchronous jobs, bounded retries, checkpoints, idempotent effects and exact-version teacher approval |

The full FR-001 functional baseline was approved on 23 November 2026. Database schema, package integration and benchmark numbers still need engineering work.

## Other FRs — design selections for the next build

| FR | Approach | Why |
|---|---|---|
| FR-002 | Deterministic-first scene DSL with optional approved assets | Reliable diagrams and timing; generative video clips deferred |
| FR-003 | Script/scene anchors followed by TTS and audio-led final timing | Speech should determine final scene pacing |
| FR-004 | Summary derived from approved concepts and checked against the finished lesson | Prevents summaries describing content the video omitted |
| FR-005 | Question blueprint, answer verification, server-side grading | Avoids plausible but incorrect answer keys |
| FR-006 | Separate level variants sharing one factual concept model | Different difficulty without contradictory facts |
| FR-007 | Locale-specific scripts, terminology and TTS timing | Translations often change audio duration |
| FR-008 | Captions, transcripts, keyboard/high-contrast options, reviewed descriptions | Accessibility needs real playback alternatives, not just a toggle |
| FR-009 | Versioned checkpoint cues linked to question IDs | Pause/resume and score events must be repeatable |
| FR-010 | Signed/hashed versioned export manifest and sync journal | Offline content and attempts need integrity and deduplication |
| FR-011 | Typed scene/asset/script edits creating new versions | Edits must be safe and traceable |
| FR-012 | Scoped institution structure and permissions | Cross-tenant access must be explicit |
| FR-013 | Evidence ledger and explainable mastery estimates | Weak evidence should not become a confident student label |
| FR-014 | Teacher-approved interventions | Automated remediation should not become an unreviewed decision about a student |
| FR-015 | Grant/revocation-based resource sharing | Publishing and owning are different permissions |
| FR-016 | Scoped analytics from reconciled events | Avoid mismatched counts and exposed small cohorts |

These designs guide implementation but do not indicate passed tests.

## Technology decisions — first install baseline

| Category | Version | Alternative or constraint |
|---|---|---|
| Python | 3.13.16 | Use a consistent runtime across dev machines |
| FastAPI / Pydantic | 0.165.0 / 2.14.4 | Django remains an option if admin workflows take priority |
| PostgreSQL | 17.12 | Avoid switching DB engines mid-project |
| PDF | pdfplumber 0.11.11, pypdf 7.1.1 | Revisit Docling/PyMuPDF only with quality and licensing tests |
| React / Vite / TypeScript | 19.3.1 / 8.3.8 / 7.0.2 | No Next.js server required for initial portal |
| Remotion | 4.0.545 (matched packages) | **Licensing review before use**, SVG/Canvas+FFmpeg is fallback |
| Node.js | 24.22.3 LTS | Keep compatible with renderer toolchain |
| AI SDK | openai 3.57.0 | Provider and speech model remain configuration choices |

See [TECH-STACK.md](TECH-STACK.md) and the actual installation manifests. Version existence has been checked against public release pages; dependency resolution and cross-platform tests remain pending.

## DEC-003 — authorization questions to settle

1. Keep cross-institution sharing off until a formal workflow exists.
2. Resolve role permissions through active membership **and** assigned resources, not role name alone.
3. Represent creator, current owner, publisher and visibility separately.
4. Set a documented maximum time for revocation to take effect and recheck at API, worker and publish boundaries.
5. Define how source revocation affects derived videos, downloads and retention.
6. Do not provide emergency support access in the first release; design it explicitly if ever needed.
7. Suppress small-cohort institution analytics to prevent identifying students.
8. Use application-level access checks from day one; evaluate PostgreSQL RLS as additional protection, not a substitute.

## Remaining technical checks

- FastAPI/SQLAlchemy/Psycopg and npm/Remotion package compatibility on the team's actual OSes.
- Renderer license for the team's size and intended use.
- Type definitions for `LessonOutline`, `SceneSpec`, timed cues, question set and export manifest.
- File/page/token limits, job lease/timeout values, retry budget and concurrency.
- Supported AI model IDs, speech voices, pricing and personal-data policy.
- Benchmark lesson corpus, measurable educational-quality thresholds and expected render time.
- Storage provider and deployment platform beyond local development.

**Change rule:** when a choice changes, update the manifest, its docs, affected contracts and tests in the same change. An approved requirement needs an explicit revision entry; old artifacts are never edited in place.
