"""Administrative provisioning; never invoke from an HTTP process or request."""

from sqlalchemy import Connection, text

TENANT_TABLES = (
    "institutions",
    "memberships",
    "role_grants",
    "campuses",
    "departments",
    "programmes",
    "academic_years",
    "semesters",
    "sections",
    "subjects",
    "subject_offerings",
    "teacher_assignments",
    "student_enrolments",
    "department_heads",
    "class_coordinators",
    "guardian_links",
    "sources",
    "offering_source_policies",
    "source_idempotency",
    "workflow_jobs",
    "extraction_cache",
    "page_ocr_cache",
    "concept_extraction_cache",
    "knowledge_publications",
    "knowledge_index_chunks",
    "lesson_blueprint_publications",
)

APPEND_ONLY_TENANT_TABLES = (
    "source_versions",
    "derived_extractions",
    "concept_extractions",
    "knowledge_reviews",
    "lesson_blueprints",
)


def grant_application_privileges(
    connection: Connection, role: str, schema: str = "public"
) -> None:
    """Grant explicit DML only to an already provisioned unprivileged login role."""
    safe = connection.scalar(
        text(
            "SELECT NOT (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication) FROM pg_roles WHERE rolname=:role"
        ),
        {"role": role},
    )
    if safe is not True:
        raise ValueError(
            "Provision a non-owner NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOCREATEDB role first"
        )
    quote = connection.dialect.identifier_preparer.quote
    grantee, namespace = quote(role), quote(schema)
    connection.execute(text(f"GRANT USAGE ON SCHEMA {namespace} TO {grantee}"))
    # PostgreSQL requires UPDATE privilege on at least one column for auth's FOR UPDATE.
    # Grant only display_name, never status or user_id; authentication algorithms stay unchanged.
    connection.execute(
        text(f"GRANT UPDATE (display_name) ON TABLE {namespace}.users TO {grantee}")
    )
    grants = {
        "SELECT": ("users", "verified_identity_attributes"),
        "SELECT, UPDATE": ("password_credentials",),
        "SELECT, INSERT, UPDATE, DELETE": (
            *TENANT_TABLES,
            "auth_sessions",
            "refresh_tokens",
            "auth_rate_limits",
        ),
        "SELECT, INSERT": (*APPEND_ONLY_TENANT_TABLES, "security_events"),
    }
    for permissions, tables in grants.items():
        for table in tables:
            connection.execute(
                text(
                    f"GRANT {permissions} ON TABLE {namespace}.{quote(table)} TO {grantee}"
                )
            )
