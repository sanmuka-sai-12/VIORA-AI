import os
import re
from typing import Any

import sqlalchemy as sa
from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql.base import ischema_names
from sqlalchemy.types import UserDefinedType
from viora.authorization.public import register_models as register_authorization
from viora.config import Settings
from viora.db import Base
from viora.identity.public import register_models as register_identity
from viora.institution.public import register_models as register_institution
from viora.knowledge.public import register_models as register_knowledge
from viora.sources.public import registered_table
from viora.workflow.public import register_models as register_workflow

register_authorization()
register_identity()
register_institution()
register_knowledge()
register_workflow()
metadata = Base.metadata
assert registered_table.metadata is metadata


class _VectorColType(UserDefinedType):
    cache_ok = True

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def get_col_spec(self, **_kw: object) -> str:
        return "vector(384)"


ischema_names.setdefault("vector", _VectorColType)


_PGVECTOR_384_COL_RE = re.compile(r'^(?:"?[^".]+"?\.)?vector\(384\)$')


def _include_object(
    _obj: object, name: str | None, type_: str, _reflected: bool, _compare_to: object
) -> bool:
    return not (type_ == "index" and name == "ix_kic_embedding_hnsw")


def _compare_type(
    migrate_context: object,
    inspected_column: sa.Column[Any],
    metadata_column: sa.Column[Any],
    inspected_type: Any,
    metadata_type: Any,
) -> bool | None:
    if (
        metadata_column.table.name != "knowledge_index_chunks"
        or metadata_column.name != "embedding"
        or getattr(metadata_type, "get_col_spec", lambda: None)() != "vector(384)"
    ):
        return None

    conn = getattr(migrate_context, "connection", None)
    if conn is None:
        return None

    schema_name = getattr(inspected_column.table, "schema", None)
    has_pgvector = bool(
        conn.scalar(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
    )
    col_sql_type = conn.scalar(
        sa.text(
            "SELECT format_type(a.atttypid, a.atttypmod) "
            "FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = COALESCE(:schema, current_schema()) "
            "AND c.relname = 'knowledge_index_chunks' "
            "AND a.attname = 'embedding' "
            "AND NOT a.attisdropped"
        ),
        {"schema": schema_name},
    )
    if has_pgvector:
        # With pgvector installed, embedding must match vector(384) and never fall back silently.
        return not bool(col_sql_type and _PGVECTOR_384_COL_RE.fullmatch(col_sql_type))

    # Without pgvector, migration 0012 intentionally uses 1-D double precision[].
    is_double_array = (
        isinstance(inspected_type, postgresql.ARRAY)
        and inspected_type.dimensions in (None, 1)
        and isinstance(inspected_type.item_type, postgresql.DOUBLE_PRECISION)
        and col_sql_type == "double precision[]"
    )
    return not is_double_array


url = (
    os.environ.get("MIGRATION_DATABASE_URL")
    or Settings.load().database_url.get_secret_value()
)

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=metadata,
        literal_binds=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=metadata,
                include_object=_include_object,
                compare_type=_compare_type,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
