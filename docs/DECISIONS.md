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
| DEC-003 | Tenant membership, resource ownership, revocation and special access policy | Core resolved; downstream policies deferred | Step 4.3A central policy, forced RLS and live non-owner tests |
| DEC-004 | Online-round core before offline-round and institutional extensions | Approved | Matches competition priorities |
| DEC-005 | Modular monolith with separate long-running workers | Selected for build | Keeps transactions and code ownership understandable |
| DEC-006 | PostgreSQL for authoritative records; protected media storage; derived indexes rebuildable | Selected for build | Reliable versions, relationships and audit history |
| DEC-007 | Database-backed durable jobs for first release; no Redis/Celery requirement | Selected for build | Fewer moving parts while worker volume is small |
| DEC-008 | Immutable source and generated artifact versions, linked by parent IDs | Selected for build | Prevents accidentally mixing outdated outputs |
| DEC-009 | Only validated scene data reaches the renderer | Selected for build | Model-generated code would be unsafe and difficult to reproduce |
| DEC-010 | Captions and keyboard playback start in the online prototype | Selected for build | Required for a usable narrated lesson |
| DEC-011 | Quiz answer keys stay on the server; offline quiz grading is practice-only | Selected for build | A downloadable package cannot protect a secret answer key |
| DEC-012 | Use a configurable AI provider interface; first SDK integration is OpenAI | Selected for build | Avoid coupling the rest of the system to one model or API shape |

## FR-001 â€” approved

| Decision | Requirement |
|---|---|
| FR-001-D01 | Plain text, topic descriptions and text-based PDFs; scanned PDFs/OCR deferred |
| FR-001-D02 | Extract section-level concepts first, then consolidate into a teaching sequence |
| FR-001-D03 | Versioned `LessonOutline` JSON with source references, grounding flags, validation and immutable versions |
| FR-001-D04 | Layout-aware extraction, section-first segmentation, bounded chunks and extraction warnings |
| FR-001-D05 | Durable asynchronous jobs, bounded retries, checkpoints, idempotent effects and exact-version teacher approval |

The full FR-001 functional baseline was approved on 23 November 2026. Database schema, package integration and benchmark numbers still need engineering work.

## Other FRs â€” design selections for the next build

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

## Technology decisions â€” first install baseline

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

## DEC-003 — authorization and tenant isolation

**Resolved in Phase 1, Step 4.3A and extended in Steps 4.4–4.5.** Authorization is centralized in `viora.authorization.public.AuthorizationService`, which composes the real identity/institution directories through `Access`. Every decision rechecks active global account, active institution, active membership, unrevoked roles and current assignments (plus active hierarchy state for non-admin assignments). Caller-supplied role claims never authorize access. Unknown actions, resource kinds, platform administrators and cross-institution sharing deny by default.

| Eligible role | Explicit actions | Required resource scope |
| --- | --- | --- |
| Institution owner / administrator | `institution.read`, `institution.update_metadata`, hierarchy management (`campus.*`, `department.*`, `programme.*`, `academic_year.*`, `semester.*`, `section.*`, `subject.*`, `offering.catalog.read`, `offering.update`, `offering.lifecycle`, `*.list`, and parent-scoped `*.create`) | Existing active institution; parent/target exists in same tenant (and active for child creation); excludes `offering.read` and `offering.teach` |
| Department head | `department.read`, `department.update_metadata`, `department.list`, `programme.list`, `programme.read`, `subject.list`, `subject.read` | Active head assignment to that active department (and active child programmes/subjects) |
| Class coordinator | `section.read`, `section.update_metadata`, `section.list`, `offering.list`, `offering.catalog.read` | Active coordinator assignment to that active section and active offerings within it |
| Subject teacher | `offering.read`, `offering.teach`, `offering.catalog.read`, `offering.list` | Active offering chain and active teacher assignment |
| Student | `offering.read`, `offering.catalog.read`, `offering.list`; `student.summary.read` | Active offering chain and active enrolment; own minimal summary respectively |
| Guardian | `student.summary.read` | Active, verified, consented guardian link to an active student member |

Leadership does not imply access to all offerings, students, files or assignments. Student summary permission permits only minimal institutional identification (student ID/display name), not global identifiers, contact details, grades, attendance, health, safeguarding or other sensitive records. A guardian link does not imply access to the student's teaching materials. No source metadata access is granted: existing sources lack creator/current owner/assignment/visibility fields. Those must be separately modeled and policy-tested before enabling `source.read` for any role.

### Database and transaction boundaries

Revision `0005_tenant_security` enables and **forces** PostgreSQL RLS on every current tenant-bearing table, including institutions, membership/role data, academic definitions, assignments, sources and security audit. `USING` and `WITH CHECK` compare tenant IDs to a transaction-local context; absent/empty context denies. Global users, credentials, verified identifiers, authentication sessions, refresh generations and rate-limit records remain global.

Runtime connections use a separate, non-owner login with no superuser, BYPASSRLS, role/database creation, replication, table ownership, schema CREATE, TRUNCATE or TRIGGER privilege (including inherited privileges). Startup and protected transactions reject unsafe roles. The administrative `grant_application_privileges` helper grants explicit DML without DDL, TRUNCATE, grant options or RLS bypass. Existing auth row locks require UPDATE on one users column; only `display_name` is granted, not account status or identity IDs. Migration credentials are supplied separately through `MIGRATION_DATABASE_URL`; application deployment uses only its runtime credentials.

The authorization service owns a READ COMMITTED transaction. It sets a candidate tenant with `set_config(..., true)` for internal membership checks, verifies membership and resource policy, and only then yields the transaction/session to the protected operation. No security-definer lookup or global membership bypass is used. All protected SQL must use that yielded session; never authorize in one transaction and execute in another. Tenant changes within the session are rejected. Commit, rollback and connection-pool reuse clear the local context. RLS protects tenant boundaries, not resource-level policy: application code must still use the central action decision and constrain SQL to the authorized resource. The database login and its ability to set context are trusted server capabilities, never client credentials or arbitrary-SQL capabilities.

### Revocation and audit

Committed membership status changes, role `revoked_at`, and assignment/enrolment/link `active=false` take effect at the next authorization check, with no role/assignment cache or token-grant lifetime. `AuthorizedTransaction.recheck` refreshes policy inside an existing READ COMMITTED transaction before a subsequent effect. An already authorized in-flight statement is not retroactively cancelled; long-running workers must open a new `reauthorize` scope per effect using their persisted initiating actor. Revoking a student membership/role also removes a guardian's eligibility to that student's summary. Revoking a teacher role disables its assignments even if assignment rows remain active.

Tenant-scoped `security_events` stores actor, action, resource, timestamp, decision/change kind and outcome, without tokens or credentials. Runtime audit privileges are SELECT/INSERT only, and RLS has no UPDATE/DELETE policy. Initial authenticated denials commit before AccessError is raised. Allowed decisions and explicit `scope.audit.change(...)` records commit atomically with protected mutations; failed transactions roll both back. Audit write failures fail closed. Unauthenticated HTTP failures remain in the existing correlation/error mechanism; they have no trusted actor for this audit contract.

### Controlled bootstrap

There is no public tenant creation or membership/role mutation permission yet. An audited administrative provisioning process (`viora.institution.bootstrap.bootstrap_institution`) uses separate administrative credentials (`BOOTSTRAP_DATABASE_URL` or `MIGRATION_DATABASE_URL`), chooses a new tenant UUID, and explicitly sets that local context inside one transaction. It creates an inactive institution, references an already provisioned verified active global user, inserts that user's active membership and initial owner role, appends a bootstrap change audit, and activates the institution only after verification. The forced RLS rules apply throughout; no disabling RLS, wildcard context, security-definer bypass or runtime owner membership is allowed. Only trusted provisioning operators may run it. Existing legacy inactive institutions require reconciliation before activation.

Cross-tenant institution discovery (`GET /api/v1/institutions`) remains deferred because `memberships` and `institutions` enforce single-tenant forced RLS without an actor-scoped cross-tenant policy or runtime `BYPASSRLS`.

### Deferred policies

Source ownership/visibility/publishing and derived-media revocation/retention; institutional membership/role management actions and transfer of ownership; detailed guardian/student disclosure and consent withdrawal workflows; cross-institution sharing; platform/emergency support access; cohort analytics suppression; worker/retrieval/media/publication implementations. These remain denied until explicitly added to the central permission matrix and tested. No role implies these capabilities.

See `docs/authorization.md` for API composition, database provisioning and verification commands.

## Remaining technical checks

- FastAPI/SQLAlchemy/Psycopg and npm/Remotion package compatibility on the team's actual OSes.
- Renderer license for the team's size and intended use.
- Type definitions for `LessonOutline`, `SceneSpec`, timed cues, question set and export manifest.
- File/page/token limits, job lease/timeout values, retry budget and concurrency.
- Supported AI model IDs, speech voices, pricing and personal-data policy.
- Benchmark lesson corpus, measurable educational-quality thresholds and expected render time.
- Storage provider and deployment platform beyond local development.

**Change rule:** when a choice changes, update the manifest, its docs, affected contracts and tests in the same change. An approved requirement needs an explicit revision entry; old artifacts are never edited in place.
