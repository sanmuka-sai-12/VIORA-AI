from alembic import context
from sqlalchemy import create_engine, pool
from viora.config import Settings
from viora.db import Base
from viora.sources.public import registered_table

metadata = Base.metadata
assert registered_table.metadata is metadata
url = Settings.load().database_url.get_secret_value()

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
