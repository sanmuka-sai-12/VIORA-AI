# Step 4.3A authorization handoff

## Public API

Import `AuthorizationService` and `AuthorizedTransaction` from `viora.authorization.public`. Shared actor/resource/context types live in `viora.contracts`; `Authentication`, `Membership`, `Authorization`, `Audit` and `ChangeAudit` are protocols in `viora.security`.

```python
service = AuthorizationService(database, authentication)
service.validate_database()  # Application startup already invokes this when auth is configured.

# Both the permission check and protected operation use this transaction.
with service.transaction(bearer, "offering.read", resource) as scope:
    result = repository_using(scope.session).read(resource.resource_id)

# Before another effect or after lengthy work:
scope.recheck(action: str, resource: ResourceRef) -> TenantContext
scope.audit.change(context: TenantContext, action: str, resource: ResourceRef) -> None

# For trusted workers, actor comes from stored job provenance, never caller JSON:
with service.reauthorize(actor, action, resource) as scope:
    ...

# Decision-only convenience; never pair this with SQL in a separate transaction:
service.require(bearer: str | None, action: str, resource: ResourceRef) -> TenantContext
```

`transaction` and `reauthorize` return context managers yielding `AuthorizedTransaction(session, context, audit, ...)`. Never manually commit, change tenant context, retain the session, or perform unapproved operations in the block. Return DTOs/materialized results, not lazy ORM data. Step 4.6 mutation endpoints lock their target and recheck actor authority before changing state. Audit changes in the same transaction as membership, role, assignment, enrolment or guardian-link mutations. Guardian verification/activation and unknown actions deny.

`GET /api/v1/institutions` authenticates the bearer, opens a short actor-scoped discovery transaction, and returns `[{tenant_id, name, roles}]` for active memberships in active institutions. It does not select a tenant or authorize later requests. The browser must continue sending the chosen `tenant_id` to tenant routes, where the normal membership/resource policy is evaluated again. Never cache discovery roles as authorization claims.

The existing source HTTP route now uses the real central service. It remains 403 for authenticated members because sources have no ownership/assignment/visibility scope. Existing injectable Access remains available for isolated service tests; production composition uses AuthorizationService and real directories.

## Database provisioning

Use separate credentials for migrations/bootstrap and application traffic. Never deploy the application with the Docker database-owner credentials. This change does not edit local `.env` or provision permanent deployment roles.

1. As the database administrator, create a fresh login such as `viora_app` with a securely provisioned password and `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT`. Do not grant it membership in migration/owner roles. Do not grant schema CREATE, table TRUNCATE or TRIGGER. Revoke PUBLIC schema CREATE where older database defaults provide it.
2. Run `alembic upgrade head` in the migration process with `MIGRATION_DATABASE_URL` pointing to the schema-owner credential. This variable is read from the process environment, not `.env`. Migration fallback to DATABASE_URL supports existing tooling/tests but must never be used to deploy owner credentials into the app.
3. In an administrative transaction call `viora.database_roles.grant_application_privileges(connection, "viora_app", "public")`. The helper grants enumerated DML only. It does not create roles, own tables, disable RLS, or grant schema CREATE. Reapply after future explicit schema/privilege changes; new tables are not automatically granted.
4. Set the runtime `DATABASE_URL` to that application login. Startup verifies role safety. All tenant-bearing tables must have enabled/forced RLS. Global auth tables remain available to the authentication implementation, which retains its existing algorithms and session transactions.

Tenant bootstrap is the controlled operator-only convention in DEC-003. Institution creation is not an action in the current policy and cannot be reached through an owner/admin role alone.

## Limits and tradeoffs

- RLS isolates tenants even if a repository forgets a tenant predicate. It does not substitute for resource authorization and does not treat possession of the database login as an untrusted-client capability.
- Revocations apply at the next policy check, including `recheck` in READ COMMITTED. They do not cancel a statement already authorized before the revocation committed.
- Audit decisions that allow operations and audit changes share the protected transaction. An operation rollback also rolls those audit rows back. Initial authenticated denials commit independently of the ensuing HTTP exception.
- The migration contains a frozen table list; the provisioning helper contains an explicit runtime privilege list. Future tenant tables require both an RLS migration and intentional privilege grants. No blanket default grants or RLS bypass are provided.
- Existing `WorkerAuthorization` remains an unimplemented downstream job adapter: future workers load job provenance and use `reauthorize`; no job/media/retrieval/publication implementation is added.

## Validation

Run tests only with `VIORA_TEST_DATABASE_URL` naming the dedicated `viora_test` database, and migrate that database first. Never substitute the development database. New fixtures create disposable schemas and random non-owner login roles, use real password-authenticated connections, and clean up their own schema/role only. The mandatory PostgreSQL CI job collects these tests with `pytest -m postgres -v`.

Gravitas policy record: compact planning, dependency-scoped context, impact verification, no delegation. Scope is RLS, authorization and transaction invariants. Executable validators are the live authorization/RLS tests, authentication regression tests and Alembic metadata/round-trip tests. Final handoff reports observed results rather than treating test collection as execution.
