"""Exercise listing SQL locally; SQLite does not validate PostgreSQL RLS."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session
from viora.identity.public import register_models as register_identity
from viora.institution.public import register_models as register_institution
from viora.sources.models import Source, SourceVersion
from viora.sources.repository import SqlSourceRepository


@pytest.fixture
def listing_session() -> Iterator[tuple[Session, list[str]]]:
    """Clone column types only into a disposable, in-memory query harness."""
    register_identity()
    register_institution()
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (Source, SourceVersion):
        Table(
            model.__tablename__, metadata,
            *(Column(column.name, JSON() if isinstance(column.type, JSONB) else column.type,
                     primary_key=column.primary_key, nullable=True)
              for column in model.__table__.columns),
        )
    metadata.create_all(engine)
    statements: list[str] = []

    def record_sql(
        connection: object, cursor: object, statement: str,
        parameters: object, context: object, executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_sql)
    with Session(engine) as session:
        yield session, statements
    engine.dispose()


def add_source(
    session: Session, tenant: UUID, offering: UUID, owner: UUID, source_id: UUID,
    *, origin: str = "teacher_resource", visibility: str = "offering_published",
    state: str = "active", version_tenant: UUID | None = None,
    with_version: bool = True,
) -> None:
    version_id = uuid4() if with_version else None
    now = datetime.now(UTC)
    session.execute(Source.__table__.insert().values(
        tenant_id=tenant, source_id=source_id, title="Source", offering_id=offering,
        creator_user_id=owner, owner_user_id=owner, origin_kind=origin,
        visibility=visibility, lifecycle_state=state, latest_version_number=int(with_version),
        latest_version_id=version_id, created_at=now, updated_at=now,
    ))
    if with_version:
        session.execute(SourceVersion.__table__.insert().values(
            tenant_id=version_tenant or tenant, source_id=source_id, version_id=version_id,
            input_format="plain_text", content_sha256="a" * 64, size_bytes=12,
        ))
    session.commit()


def test_listing_loads_latest_summary_in_one_select_and_preserves_cursor(
    listing_session: tuple[Session, list[str]],
) -> None:
    session, statements = listing_session
    tenant, offering, actor = uuid4(), uuid4(), uuid4()
    first, second = UUID(int=1), UUID(int=2)
    add_source(session, tenant, offering, actor, first)
    add_source(session, tenant, offering, actor, second, with_version=False)
    repository = SqlSourceRepository(session)
    page = repository.list_offering_sources(
        tenant, offering, actor_id=actor, is_assigned_teacher=False,
        is_enrolled_student=True, teachers_can_view_student_submissions=False,
        origin_kind=None, lifecycle_state=None, cursor=None, limit=1,
    )
    assert [item.source_id for item in page.items] == [first]
    assert page.items[0].latest_digest == "a" * 64
    assert page.items[0].latest_size_bytes == 12
    assert page.next_cursor == str(first)
    assert len(statements) == 1
    assert "extracted_document" not in statements[0]
    statements.clear()
    following = repository.list_offering_sources(
        tenant, offering, actor_id=actor, is_assigned_teacher=False,
        is_enrolled_student=True, teachers_can_view_student_submissions=False,
        origin_kind=None, lifecycle_state=None, cursor=first, limit=1,
    )
    assert [item.source_id for item in following.items] == [second]
    assert following.items[0].latest_digest is None
    assert following.next_cursor is None
    assert len(statements) == 1


@pytest.mark.parametrize("teacher,student,review_allowed,expected", [
    (False, True, False, {1, 4}),
    (True, False, False, {1, 2, 3}),
    (True, False, True, {1, 2, 3, 5}),
    (True, True, True, {1, 2, 3, 4, 5}),
    (False, False, True, set()),
])
def test_listing_preserves_visibility_and_tenant_isolation(
    listing_session: tuple[Session, list[str]], teacher: bool, student: bool,
    review_allowed: bool, expected: set[int],
) -> None:
    session, statements = listing_session
    tenant, offering, actor, other = uuid4(), uuid4(), uuid4(), uuid4()
    add_source(session, tenant, offering, other, UUID(int=1), version_tenant=uuid4())
    add_source(session, tenant, offering, other, UUID(int=2), visibility="teacher_only")
    add_source(session, tenant, offering, other, UUID(int=3), state="archived")
    add_source(session, tenant, offering, actor, UUID(int=4), origin="student_submission", visibility="owner_private")
    add_source(session, tenant, offering, other, UUID(int=5), origin="student_submission", visibility="teacher_review")
    add_source(session, tenant, offering, other, UUID(int=6), origin="student_submission", visibility="owner_private")
    add_source(session, uuid4(), offering, actor, UUID(int=7))
    add_source(session, tenant, uuid4(), actor, UUID(int=8))
    page = SqlSourceRepository(session).list_offering_sources(
        tenant, offering, actor_id=actor, is_assigned_teacher=teacher,
        is_enrolled_student=student, teachers_can_view_student_submissions=review_allowed,
        origin_kind=None, lifecycle_state=None, cursor=None, limit=25,
    )
    assert {item.source_id.int for item in page.items} == expected
    assert all(item.latest_digest is None for item in page.items if item.source_id.int == 1)
    assert len(statements) == (1 if expected else 0)
