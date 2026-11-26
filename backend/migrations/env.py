import os

from alembic import context
from sqlalchemy import create_engine, pool
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
url = (
    os.environ.get("MIGRATION_DATABASE_URL")
    or Settings.load().database_url.get_secret_value()
)

if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
