"""Exercise the Phase 0 upgrade path in a dedicated PostgreSQL schema."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.schema import CreateSchema, DropSchema

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "0001_sources"
REVISION = "0002_identity_academics"


def test_identity_revision_follows_sources() -> None:
    history = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    assert history.get_heads() == ["0007_actor_institution_discovery"]
    assert (
        history.get_revision("0007_actor_institution_discovery").down_revision
        == "0006_academic_lifecycle"
    )
    assert (
        history.get_revision("0006_academic_lifecycle").down_revision
        == "0005_tenant_security"
    )
    assert (
        history.get_revision("0005_tenant_security").down_revision
        == "0004_auth_rate_limits"
    )
    assert (
        history.get_revision("0004_auth_rate_limits").down_revision
        == "0003_auth_sessions"
    )
    assert history.get_revision("0003_auth_sessions").down_revision == REVISION
    assert history.get_revision(REVISION).down_revision == BASELINE


@pytest.mark.postgres
def test_existing_source_survives_migration_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_url = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Dedicated PostgreSQL integration database required")
    schema = f"migration_{uuid4().hex}"
    url = make_url(raw_url)
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL", scoped_url.render_as_string(hide_password=False)
    )
    admin = create_engine(url)
    scoped = create_engine(scoped_url)
    config = Config(str(ROOT / "alembic.ini"))
    tenant, source = uuid4(), uuid4()
    try:
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        try:
            command.upgrade(config, BASELINE)
            with scoped.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO sources (tenant_id, source_id, title) VALUES (:tenant, :source, 'Legacy')"
                    ),
                    {"tenant": tenant, "source": source},
                )
            command.upgrade(config, "head")
            command.check(config)
            with scoped.connect() as connection:
                assert (
                    connection.scalar(
                        text(
                            "SELECT active FROM institutions WHERE tenant_id = :tenant"
                        ),
                        {"tenant": tenant},
                    )
                    is False
                )
            command.downgrade(config, BASELINE)
            with scoped.connect() as connection:
                assert set(inspect(connection).get_table_names()) == {
                    "alembic_version",
                    "sources",
                }
                assert connection.execute(
                    text("SELECT tenant_id, source_id, title FROM sources")
                ).one() == (tenant, source, "Legacy")
            command.upgrade(config, "head")
            command.check(config)
        finally:
            scoped.dispose()
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
    finally:
        admin.dispose()
