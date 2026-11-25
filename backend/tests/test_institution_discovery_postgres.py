"""Live tests for actor-scoped institution discovery and RLS context cleanup."""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from psycopg import sql
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.config import AuthSettings, Settings
from viora.database_roles import grant_application_privileges
from viora.db import Database
from viora.identity.public import AuthService, IdentityAttributeKind, provision_account
from viora.institution.bootstrap import bootstrap_institution
from viora.main import create_app

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("discovery test passphrase 42")


@dataclass(frozen=True)
class DiscoveryEnv:
    admin: Engine
    owner_db: Database
    app_db: Database
    app_url: str


@pytest.fixture(scope="module")
def discovery_env() -> Iterator[DiscoveryEnv]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"discovery_{uuid4().hex}", f"discovery_app_{uuid4().hex}"
    password = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    owner_url = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=password).render_as_string(
        hide_password=False
    )
    admin = create_engine(scoped)
    owner_db = Database(Settings(database_url=SecretStr(owner_url)))
    app_db = Database(Settings(database_url=SecretStr(app_url), pool_size=1))
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
        connection.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS "
                "NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", owner_url)
            patch.setenv("MIGRATION_DATABASE_URL", owner_url)
            command.upgrade(
                Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")),
                "head",
            )
        with admin.begin() as connection:
            grant_application_privileges(connection, role, schema)
        yield DiscoveryEnv(admin, owner_db, app_db, app_url)
    finally:
        app_db.close()
        owner_db.close()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
            connection.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _account(env: DiscoveryEnv, label: str) -> tuple[UUID, str]:
    user_id = uuid4()
    email = f"{label}-{user_id}@example.test"
    provision_account(
        env.owner_db.sessions,
        email=email,
        display_name=label,
        password=PASSWORD,
        user_id=user_id,
    )
    return user_id, email


def test_discovery_is_actor_scoped_minimal_and_pool_safe(
    discovery_env: DiscoveryEnv,
) -> None:
    env = discovery_env
    actor, email = _account(env, "Actor")
    other, _ = _account(env, "Other")
    schools = [
        bootstrap_institution(env.owner_db.sessions, name=name, owner_user_id=owner)
        for name, owner in (
            ("Alpha", actor),
            ("Beta", other),
            ("Revoked", other),
            ("Inactive", other),
        )
    ]
    alpha, beta, revoked, inactive = schools
    with env.admin.begin() as connection:
        for tenant, status in (
            (beta.tenant_id, "active"),
            (revoked.tenant_id, "revoked"),
            (inactive.tenant_id, "active"),
        ):
            connection.execute(
                text("INSERT INTO memberships VALUES (:tenant,:actor,:status)"),
                {"tenant": tenant, "actor": actor, "status": status},
            )
        connection.execute(
            text(
                "INSERT INTO role_grants (tenant_id,user_id,role) VALUES "
                "(:beta,:actor,'student'),(:beta,:actor,'subject_teacher')"
            ),
            {"beta": beta.tenant_id, "actor": actor},
        )
        connection.execute(
            text("UPDATE institutions SET active=false WHERE tenant_id=:tenant"),
            {"tenant": inactive.tenant_id},
        )

    settings = Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        auth=AuthSettings(signing_key=SecretStr("d" * 64)),
    )
    assert settings.auth is not None
    token = AuthService(env.app_db.sessions, settings.auth).login(
        IdentityAttributeKind.email, email, PASSWORD
    )
    headers = {"Authorization": f"Bearer {token.access_token.get_secret_value()}"}
    with TestClient(create_app(settings, env.app_db)) as client:
        response = client.get("/api/v1/institutions", headers=headers)
        assert response.status_code == 200
        assert response.json() == [
            {
                "tenant_id": str(alpha.tenant_id),
                "name": "Alpha",
                "roles": ["institution_owner"],
            },
            {
                "tenant_id": str(beta.tenant_id),
                "name": "Beta",
                "roles": ["student", "subject_teacher"],
            },
        ]
        assert client.get("/api/v1/institutions").status_code == 401

    # The same one-connection pool must not retain actor context.
    with env.app_db.sessions.begin() as session:
        assert session.scalar(
            text("SELECT current_setting('viora.actor_id', true)")
        ) in {None, ""}
        assert session.scalar(text("SELECT count(*) FROM institutions")) == 0
        assert session.scalar(text("SELECT count(*) FROM memberships")) == 0
