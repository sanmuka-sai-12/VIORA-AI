# VIORA-AI engineering rules

## Before editing
- Work only in the VIORA-AI repository. Check `git rev-parse --show-toplevel`, `git status` and the current branch. Never stage files outside it.
- Read `docs/PHASE-WISE-ACTION-PLAN.md`, `docs/REQUIREMENTS.md`, `docs/DECISIONS.md`, `docs/ARCHITECTURE-V4-FULL.md`, `docs/TECH-STACK.md` and `docs/complete-project-detailed-end-to-end.md` for the relevant scope. The phase plan governs delivery and module boundaries; the requirements and product blueprint govern feature scope. Report contradictions rather than silently overriding them.
- Work on one assigned issue/branch. Name allowed edit paths and affected contracts before implementation. Stop and report conflicts with another owner's work. Do not reset, discard or overwrite other changes.

## Architecture
- One FastAPI modular monolith, role-aware React/TypeScript frontend, PostgreSQL and separate durable worker processes. Do not introduce microservices or new infrastructure without an approved decision.
- Each domain owns its router, schemas, services, repositories, persistence and tests. Router calls service; service calls repository or port. Cross-domain calls use public typed interfaces, not private repositories, direct writes to another domain's tables or HTTP loopback calls.
- Keep composition, shared core, dependency manifests, lockfiles, migration heads, generated clients and shared contracts under coordinated ownership. Change contracts in a reviewed PR before changing consumers. Generate clients from the authoritative contract; do not hand-edit generated types.
- Use deterministic fixtures/fake adapters for unfinished dependencies in tests only. Never expose fake authentication, fake success responses or mock production data paths. Real dependencies must be integrated and contract-tested as they land.

## Security and reliability
- Deny access by default. Verify identity, active tenant membership, resource/action permissions and assignments at protected API, worker, retrieval and publication boundaries. Do not implement exceptional admin access or cross-tenant sharing until policy is approved. Use synthetic data until privacy policies are resolved.
- Treat uploaded content and model output as untrusted data. Validate typed outputs; never execute arbitrary model-generated code. Filter RAG evidence by access before prompt assembly. Preserve source lineage and immutable artifact versions; generation requires approval of the exact outline version.
- Use durable jobs, bounded retries, checkpoints and idempotent effects; do not promise exactly-once external model calls. Use transactional outbox for cross-domain events where required. Keep assessment keys server-side.
- Keep secrets out of Git and logs. Never read or modify files outside the assigned workspace without explicit permission. Never run destructive database operations, install unapproved dependencies, push or merge without permission.

## Verification and handoff
- Read existing manifests; verify compatibility in a clean environment before changing pins. Report install failures with exact evidence; propose dependency changes for review.
- Add focused unit and contract tests; use real PostgreSQL for DB integration and tenant isolation tests. Mock paid AI calls in routine CI. Run relevant checks and report actual results, not assumed success.
- Keep changes small. Finish with changed files, contract changes, tests and actual results, outstanding blockers and integration notes. Do not claim unimplemented functionality is complete. Do not commit/push unless explicitly requested.
