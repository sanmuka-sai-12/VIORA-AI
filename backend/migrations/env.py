import os

from alembic import context
from sqlalchemy import create_engine, pool
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


def _include_object(
    _obj: object, name: str | None, type_: str, _reflected: bool, _compare_to: object
) -> bool:
    return not (type_ == "index" and name == "ix_kic_embedding_hnsw")


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
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
