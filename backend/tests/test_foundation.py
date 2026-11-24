import ast
import os
from pathlib import Path
from unittest.mock import create_autospec
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from viora.config import Settings
from viora.contracts import Actor, ErrorResponse, TenantContext
from viora.db import Database
from viora.main import create_app
from viora.security import Access, Authentication, Authorization, Membership
from viora.sources.models import Source
from viora.sources.ports import Sources
from viora.sources.schemas import SourceSummary
from viora.sources.service import read_source

TEST_URL = "postgresql+psycopg://localhost/viora_test"


def settings() -> Settings:
    return Settings.model_validate(
        {"database_url": TEST_URL, "cors_origins": ["https://viora.example"]}
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"database_url": "sqlite:///local"},
        {"pool_size": 0},
        {"cors_origins": ["*"]},
        {"environment": "unknown"},
    ],
)
def test_invalid_config(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings.model_validate({"database_url": TEST_URL, **changes})


def test_lifecycle_health_and_errors() -> None:
    db = create_autospec(Database, instance=True)
    db.sessions = Database(settings()).sessions
    app = create_app(settings(), db)
    rid = str(uuid4())
    with TestClient(app) as client:
        response = client.get("/api/v1/health/live", headers={"X-Request-ID": rid})
        assert (
            response.json() == {"status": "live"}
            and response.headers["X-Request-ID"] == rid
        )
        db.ready.assert_not_called()
        db.ready.return_value = True
        assert client.get("/api/v1/health/ready").status_code == 200
        db.ready.return_value = False
        db.ready.side_effect = [False, RuntimeError("private diagnostic")]
        for expected in (503, 500):
            result = client.get(
                "/api/v1/health/ready", headers={"X-Request-ID": "invalid"}
            )
            assert result.status_code == expected
            assert ErrorResponse.model_validate(result.json()).request_id == UUID(
                result.headers["X-Request-ID"]
            )
            assert "private diagnostic" not in result.text
        assert client.get("/missing").status_code == 404
        assert client.get("/api/v1/tenants/bad/sources/bad").status_code == 422
        for origin, permitted in [
            ("https://viora.example", True),
            ("https://other.example", False),
        ]:
            result = client.get("/api/v1/health/live", headers={"Origin": origin})
            assert ("access-control-allow-origin" in result.headers) == permitted
    db.close.assert_called_once()


def test_no_client_identity_bypass() -> None:
    with TestClient(create_app(settings())) as client:
        result = client.get(
            f"/api/v1/tenants/{uuid4()}/sources/{uuid4()}",
            headers={"Authorization": "Bearer forged", "X-Actor-ID": str(uuid4())},
        )
        assert result.status_code == 401
        assert result.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "decision",
    ["allow", "inactive", "wrong_tenant", "wrong_actor", "unassigned", "no_roles"],
)
def test_policy_before_repository(decision: str) -> None:
    from viora.security import AccessError

    auth = create_autospec(Authentication, instance=True)
    memberships = create_autospec(Membership, instance=True)
    policy = create_autospec(Authorization, instance=True)
    repo = create_autospec(Sources, instance=True)
    actor, tenant, source = uuid4(), uuid4(), uuid4()
    auth.verify.return_value = Actor(actor_id=actor)
    memberships.active.return_value = (
        None
        if decision == "inactive"
        else TenantContext(
            actor_id=uuid4() if decision == "wrong_actor" else actor,
            tenant_id=uuid4() if decision == "wrong_tenant" else tenant,
            roles=frozenset() if decision == "no_roles" else frozenset({"teacher"}),
        )
    )
    policy.permits.return_value = decision != "unassigned"
    summary = SourceSummary(source_id=source, title="Example")
    repo.find.return_value = summary
    access = Access(auth, memberships, policy)
    if decision == "allow":
        assert read_source(access, repo, "token", tenant, source) == summary
        repo.find.assert_called_once_with(tenant, source)
    else:
        with pytest.raises(AccessError) as denied:
            read_source(access, repo, "token", tenant, source)
        assert denied.value.status == 403
        repo.find.assert_not_called()


@pytest.mark.postgres
def test_migrated_postgres() -> None:
    url = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not url:
        pytest.skip(
            "VIORA_TEST_DATABASE_URL required (migrated PostgreSQL test database)"
        )
    db = Database(Settings.model_validate({"database_url": url}))
    try:
        assert db.ready()
        with db.sessions.begin() as session:
            session.execute(select(Source).limit(1))
    finally:
        db.close()


def check_module_import_rules(
    module: str, targets: list[str], domains: set[str]
) -> None:
    for target in targets:
        for domain in domains:
            domain_prefix = f"viora.{domain}."
            if target.startswith(domain_prefix) and not module.startswith(
                domain_prefix
            ):
                assert (
                    module in {"viora.main", "viora.export_contracts"}
                    or module.split(".")[1] in domains
                ) and target == f"viora.{domain}.public", (
                    f"Forbidden cross-domain access from {module} to private internal {target}"
                )
        if module.endswith((".service", ".ports", ".schemas")):
            assert not target.endswith(
                (".models", ".repository", ".router", ".db")
            ), f"Dependency inversion violated: {module} cannot import {target}"


def test_import_boundaries() -> None:
    from graphlib import TopologicalSorter
    from importlib.util import resolve_name

    root = Path(__file__).resolve().parents[1]
    paths = {
        (p.relative_to(root).with_suffix("").as_posix().replace("/", ".")): p
        for p in (root / "viora").rglob("*.py")
    }
    graph: TopologicalSorter[str] = TopologicalSorter()
    viora_dir = root / "viora"
    domains = {
        p.name for p in viora_dir.iterdir() if p.is_dir() and (p / "public.py").exists()
    }
    for module, path in paths.items():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = resolve_name(
                    "." * node.level + (node.module or ""), module.rpartition(".")[0]
                )
                targets.extend([base, *(f"{base}.{a.name}" for a in node.names)])
            valid_targets = [t for t in targets if t in paths]
            for target in valid_targets:
                graph.add(module, target)
            check_module_import_rules(module, valid_targets, domains)
    tuple(graph.static_order())


def test_import_boundaries_positive_and_negative_rules() -> None:
    domains = {"sources", "lessons"}

    # Positive: Another domain module consuming an external domain's public interface
    check_module_import_rules(
        "viora.lessons.service", ["viora.sources.public"], domains
    )

    # Positive: App composition consuming public interfaces
    check_module_import_rules(
        "viora.main", ["viora.sources.public", "viora.lessons.public"], domains
    )

    # Negative: Another domain attempting to import private repository directly
    with pytest.raises(AssertionError, match="Forbidden cross-domain access"):
        check_module_import_rules(
            "viora.lessons.service", ["viora.sources.repository"], domains
        )

    # Negative: Another domain attempting to import private models directly
    with pytest.raises(AssertionError, match="Forbidden cross-domain access"):
        check_module_import_rules(
            "viora.lessons.service", ["viora.sources.models"], domains
        )

    # Negative: Domain service violating dependency inversion by importing repository
    with pytest.raises(AssertionError, match="Dependency inversion violated"):
        check_module_import_rules(
            "viora.sources.service", ["viora.sources.repository"], domains
        )

