"""Live PostgreSQL integration tests for Step 4.6: memberships, role grants, assignments, and guardian links."""

import os
import secrets
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Event
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
from viora.identity.public import (
    AccountStatus,
    AuthService,
    IdentityAttributeKind,
    provision_account,
)
from viora.identity.repository import SqlMembershipRepository
from viora.institution.bootstrap import bootstrap_institution
from viora.main import create_app

pytestmark = pytest.mark.postgres
PASSWORD = SecretStr("membership test passphrase 42")


@dataclass(frozen=True)
class Step46Env:
    admin: Engine
    admin_db: Database
    app_db: Database
    app_url: str


@pytest.fixture(scope="module")
def env46() -> Iterator[Step46Env]:
    raw = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated viora_test database required")
    url = make_url(raw)
    if url.database != "viora_test":
        pytest.fail("Requires dedicated viora_test database")
    schema, role = f"step46_{uuid4().hex}", f"step46_app_{uuid4().hex}"
    password = secrets.token_urlsafe(32)
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped.render_as_string(hide_password=False)
    app_url = scoped.set(username=role, password=password).render_as_string(
        hide_password=False
    )
    admin = create_engine(scoped)
    admin_db = Database(Settings(database_url=SecretStr(scoped_str)))
    app_db = Database(Settings(database_url=SecretStr(app_url)))
    with admin.begin() as conn:
        conn.execute(CreateSchema(schema))
        conn.connection.driver_connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", scoped_str)
            mp.setenv("MIGRATION_DATABASE_URL", scoped_str)
            cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
            command.upgrade(cfg, "head")
            command.check(cfg)
        with admin.begin() as conn:
            conn.execute(text("REVOKE CREATE ON SCHEMA public FROM PUBLIC"))
            grant_application_privileges(conn, role, schema)
        yield Step46Env(admin, admin_db, app_db, app_url)
    finally:
        app_db.close()
        admin_db.close()
        with admin.begin() as conn:
            conn.execute(DropSchema(schema, cascade=True))
            conn.connection.driver_connection.execute(
                sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
            )
        admin.dispose()


def _settings(env: Step46Env) -> Settings:
    return Settings(
        database_url=SecretStr(env.app_url),
        environment="test",
        cors_origins=("https://app.viora.example",),
        auth=AuthSettings(signing_key=SecretStr("m" * 64)),
    )


def _user(
    env: Step46Env,
    name: str,
    *,
    status: AccountStatus = AccountStatus.active,
    verified: bool = True,
) -> tuple[UUID, str]:
    uid = uuid4()
    email = f"{name}-{uid}@example.test"
    if verified:
        provision_account(
            env.admin_db.sessions,
            email=email,
            display_name=name,
            password=PASSWORD,
            status=status,
            user_id=uid,
        )
        return uid, email
    with env.admin.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (user_id, display_name, status) VALUES (:u, :n, :s)"
            ),
            {"u": uid, "n": name, "s": status.value},
        )
    return uid, email


def _token(env: Step46Env, settings: Settings, email: str) -> dict[str, str]:
    assert settings.auth is not None
    pair = AuthService(env.app_db.sessions, settings.auth).login(
        IdentityAttributeKind.email, email, PASSWORD
    )
    return {"Authorization": f"Bearer {pair.access_token.get_secret_value()}"}


def test_membership_roles_escalation_and_concurrent_last_owner(
    env46: Step46Env,
) -> None:
    owner_1, email_o1 = _user(env46, "Owner One")
    owner_2, email_o2 = _user(env46, "Owner Two")
    admin_id, email_adm = _user(env46, "Admin One")
    teacher_id, email_t = _user(env46, "Teacher One")
    unverified_id, _ = _user(env46, "Unverified", verified=False)

    school_a = bootstrap_institution(
        env46.admin_db.sessions, name="Membership A", owner_user_id=owner_1
    )
    school_b = bootstrap_institution(
        env46.admin_db.sessions, name="Membership B", owner_user_id=owner_2
    )
    tid = school_a.tenant_id
    settings = _settings(env46)
    o1_hdr = _token(env46, settings, email_o1)
    o2_hdr = _token(env46, settings, email_o2)

    with TestClient(create_app(settings, env46.app_db)) as client:
        # Unverified user rejected
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships",
                headers=o1_hdr,
                json={"user_id": str(unverified_id)},
            ).status_code
            == 409
        )

        # Associate admin, teacher, and co-owner to school A
        for uid in (admin_id, teacher_id, owner_2):
            r = client.post(
                f"/api/v1/tenants/{tid}/memberships",
                headers=o1_hdr,
                json={"user_id": str(uid)},
            )
            assert r.status_code == 201
            assert r.json()["status"] == "active"

        # Duplicate membership rejected
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships",
                headers=o1_hdr,
                json={"user_id": str(admin_id)},
            ).status_code
            == 409
        )

        # List members with pagination
        p1 = client.get(f"/api/v1/tenants/{tid}/memberships?limit=2", headers=o1_hdr)
        assert p1.status_code == 200
        assert len(p1.json()["items"]) == 2
        assert p1.json()["next_cursor"] is not None
        p2 = client.get(
            f"/api/v1/tenants/{tid}/memberships?limit=2&cursor={p1.json()['next_cursor']}",
            headers=o1_hdr,
        )
        assert len(p2.json()["items"]) == 2

        # Owner grants institution_admin and institution_owner roles
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{admin_id}/roles",
                headers=o1_hdr,
                json={"role": "institution_admin"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{owner_2}/roles",
                headers=o1_hdr,
                json={"role": "institution_owner"},
            ).status_code
            == 201
        )

        # Duplicate role grant rejected
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{admin_id}/roles",
                headers=o1_hdr,
                json={"role": "institution_admin"},
            ).status_code
            == 409
        )

        adm_hdr = _token(env46, settings, email_adm)
        t_hdr = _token(env46, settings, email_t)

        # Unauthorized role escalation & self-promotion blocked
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{admin_id}/roles",
                headers=adm_hdr,
                json={"role": "institution_owner"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/roles",
                headers=adm_hdr,
                json={"role": "institution_owner"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/roles",
                headers=adm_hdr,
                json={"role": "institution_admin"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{admin_id}/roles",
                headers=adm_hdr,
                json={"role": "subject_teacher"},
            ).status_code
            == 403
        )
        # Admin cannot suspend/revoke an owner or self-suspend
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{owner_1}/lifecycle",
                headers=adm_hdr,
                json={"status": "revoked"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{owner_1}/roles/institution_owner/revoke",
                headers=adm_hdr,
            ).status_code
            == 403
        )
        # Non-admin cannot grant/list
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/roles",
                headers=t_hdr,
                json={"role": "student"},
            ).status_code
            == 403
        )

        # Admin can grant non-admin role to teacher, suspend, reactivate, and revoke
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/roles",
                headers=adm_hdr,
                json={"role": "subject_teacher"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/lifecycle",
                headers=adm_hdr,
                json={"status": "disabled"},
            ).json()["status"]
            == "disabled"
        )
        reactivated = client.post(
            f"/api/v1/tenants/{tid}/memberships/{teacher_id}/lifecycle",
            headers=adm_hdr,
            json={"status": "active"},
        )
        assert reactivated.json()["status"] == "active"
        assert reactivated.json()["roles"] == []

        # School B owner has no authority in School A
        assert (
            client.get(
                f"/api/v1/tenants/{school_b.tenant_id}/memberships", headers=o1_hdr
            ).status_code
            == 403
        )

        # Concurrent last-owner protection: owner_1 tries to revoke owner_2 while owner_2 tries to revoke owner_1!
        barrier = Barrier(2)

        def revoke_other(hdr: dict[str, str], target: UUID) -> int:
            with TestClient(create_app(settings, env46.app_db)) as c:
                barrier.wait(timeout=5)
                resp = c.post(
                    f"/api/v1/tenants/{tid}/memberships/{target}/roles/institution_owner/revoke",
                    headers=hdr,
                )
                return resp.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(
                fut.result()
                for fut in [
                    pool.submit(revoke_other, o1_hdr, owner_2),
                    pool.submit(revoke_other, o2_hdr, owner_1),
                ]
            )
        assert results == [200, 409]

        # Determine surviving owner and verify suspending/revoking the last active owner fails 409
        with env46.admin.connect() as conn:
            surviving = conn.scalar(
                text(
                    "SELECT user_id FROM role_grants WHERE tenant_id=:t AND role='institution_owner' AND revoked_at IS NULL"
                ),
                {"t": tid},
            )
        assert surviving in {owner_1, owner_2}
        surv_hdr = o1_hdr if surviving == owner_1 else o2_hdr
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{surviving}/lifecycle",
                headers=surv_hdr,
                json={"status": "revoked"},
            ).status_code
            == 409
        )


def test_authorization_is_rechecked_after_mutation_lock(
    env46: Step46Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_id, owner_email = _user(env46, "Stale Owner")
    backup_id, _ = _user(env46, "Backup Owner")
    target_id, _ = _user(env46, "Mutation Target")
    school = bootstrap_institution(
        env46.admin_db.sessions, name="Lock Recheck", owner_user_id=owner_id
    )
    tenant_id = school.tenant_id
    settings = _settings(env46)
    owner_headers = _token(env46, settings, owner_email)
    with env46.admin.begin() as connection:
        for user_id in (backup_id, target_id):
            connection.execute(
                text("INSERT INTO memberships VALUES (:tenant,:user,'active')"),
                {"tenant": tenant_id, "user": user_id},
            )
        connection.execute(
            text(
                "INSERT INTO role_grants (tenant_id,user_id,role) "
                "VALUES (:tenant,:backup,'institution_owner')"
            ),
            {"tenant": tenant_id, "backup": backup_id},
        )

    waiting = Event()
    original = SqlMembershipRepository.lock_member

    def observed_lock(
        repository: SqlMembershipRepository, locked_tenant: UUID, user_id: UUID
    ) -> bool:
        waiting.set()
        return original(repository, locked_tenant, user_id)

    monkeypatch.setattr(SqlMembershipRepository, "lock_member", observed_lock)
    with env46.admin.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(
            text(
                "SELECT user_id FROM memberships WHERE tenant_id=:tenant "
                "AND user_id=:user FOR UPDATE"
            ),
            {"tenant": tenant_id, "user": target_id},
        )

        def grant_after_initial_authorization() -> int:
            with TestClient(create_app(settings, env46.app_db)) as client:
                return client.post(
                    f"/api/v1/tenants/{tenant_id}/memberships/{target_id}/roles",
                    headers=owner_headers,
                    json={"role": "subject_teacher"},
                ).status_code

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(grant_after_initial_authorization)
            assert waiting.wait(timeout=5)
            with env46.admin.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE role_grants SET revoked_at=now() "
                        "WHERE tenant_id=:tenant AND user_id=:owner "
                        "AND role='institution_owner'"
                    ),
                    {"tenant": tenant_id, "owner": owner_id},
                )
            transaction.commit()
            assert future.result(timeout=10) == 403

    with env46.admin.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM role_grants WHERE tenant_id=:tenant "
                    "AND user_id=:target AND role='subject_teacher' "
                    "AND revoked_at IS NULL"
                ),
                {"tenant": tenant_id, "target": target_id},
            )
            == 0
        )


def test_assignments_and_revocation_lifecycle(env46: Step46Env) -> None:
    owner_id, email_o = _user(env46, "Owner")
    head_id, email_h = _user(env46, "Head")
    coord_id, email_c = _user(env46, "Coord")
    teacher_id, email_t = _user(env46, "Teacher")
    student_id, email_s = _user(env46, "Student")
    outsider_id, _ = _user(env46, "Outsider")

    school_a = bootstrap_institution(
        env46.admin_db.sessions, name="Assign A", owner_user_id=owner_id
    )
    school_b = bootstrap_institution(
        env46.admin_db.sessions, name="Assign B", owner_user_id=outsider_id
    )
    tid = school_a.tenant_id
    settings = _settings(env46)
    o_hdr = _token(env46, settings, email_o)

    with TestClient(create_app(settings, env46.app_db)) as client:
        for uid, role in [
            (head_id, "department_head"),
            (coord_id, "class_coordinator"),
            (teacher_id, "subject_teacher"),
            (student_id, "student"),
        ]:
            assert (
                client.post(
                    f"/api/v1/tenants/{tid}/memberships",
                    headers=o_hdr,
                    json={"user_id": str(uid)},
                ).status_code
                == 201
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{tid}/memberships/{uid}/roles",
                    headers=o_hdr,
                    json={"role": role},
                ).status_code
                == 201
            )

        dept = client.post(
            f"/api/v1/tenants/{tid}/departments",
            headers=o_hdr,
            json={"name": "Physics"},
        ).json()["id"]
        subj = client.post(
            f"/api/v1/tenants/{tid}/subjects",
            headers=o_hdr,
            json={"name": "Optics", "department_id": dept},
        ).json()["id"]
        yr = client.post(
            f"/api/v1/tenants/{tid}/academic-years",
            headers=o_hdr,
            json={"name": "2027"},
        ).json()["id"]
        sem = client.post(
            f"/api/v1/tenants/{tid}/semesters",
            headers=o_hdr,
            json={"name": "Fall", "academic_year_id": yr},
        ).json()["id"]
        sec = client.post(
            f"/api/v1/tenants/{tid}/sections",
            headers=o_hdr,
            json={"name": "Phy-A", "semester_id": sem},
        ).json()["id"]
        off = client.post(
            f"/api/v1/tenants/{tid}/offerings",
            headers=o_hdr,
            json={"name": "Optics Phy-A", "section_id": sec, "subject_id": subj},
        ).json()["id"]

        # Wrong-role and cross-tenant user assignments rejected (409)
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/departments/{dept}/heads",
                headers=o_hdr,
                json={"user_id": str(student_id)},
            ).status_code
            == 409
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/teachers",
                headers=o_hdr,
                json={"user_id": str(outsider_id)},
            ).status_code
            == 409
        )
        # Cross-tenant resource rejected (403)
        assert (
            client.post(
                f"/api/v1/tenants/{school_b.tenant_id}/offerings/{off}/teachers",
                headers=o_hdr,
                json={"user_id": str(teacher_id)},
            ).status_code
            == 403
        )

        # Valid assignments
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/departments/{dept}/heads",
                headers=o_hdr,
                json={"user_id": str(head_id)},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/sections/{sec}/coordinators",
                headers=o_hdr,
                json={"user_id": str(coord_id)},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/teachers",
                headers=o_hdr,
                json={"user_id": str(teacher_id)},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments",
                headers=o_hdr,
                json={"user_id": str(student_id)},
            ).status_code
            == 201
        )

        # Duplicate assignments rejected (409) and rolls back change audit atomically
        with env46.admin.connect() as conn:
            enrol_changes_before = conn.scalar(
                text(
                    "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action='offering.enrolment.create'"
                ),
                {"t": tid},
            )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments",
                headers=o_hdr,
                json={"user_id": str(student_id)},
            ).status_code
            == 409
        )
        with env46.admin.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action='offering.enrolment.create'"
                    ),
                    {"t": tid},
                )
                == enrol_changes_before
            )

        h_hdr = _token(env46, settings, email_h)
        c_hdr = _token(env46, settings, email_c)
        t_hdr = _token(env46, settings, email_t)
        s_hdr = _token(env46, settings, email_s)

        # Assigned roles gain scoped access immediately
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/departments/{dept}", headers=h_hdr
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/sections/{sec}", headers=c_hdr
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments", headers=t_hdr
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments", headers=s_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/offerings/{off}", headers=s_hdr
            ).status_code
            == 200
        )

        # Revoke teacher role -> teacher immediately loses offering access even while assignment active
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/memberships/{teacher_id}/roles/subject_teacher/revoke",
                headers=o_hdr,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/offerings/{off}", headers=t_hdr
            ).status_code
            == 403
        )

        # Withdraw student -> student immediately loses offering access
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments/{student_id}/withdraw",
                headers=o_hdr,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/offerings/{off}", headers=s_hdr
            ).status_code
            == 403
        )

        # Archive offering -> cannot assign teacher/enrolment to archived offering
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/lifecycle",
                headers=o_hdr,
                json={"active": False},
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/offerings/{off}/enrolments",
                headers=o_hdr,
                json={"user_id": str(student_id)},
            ).status_code
            == 403
        )

        # Revoke head & coordinator assignments
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/departments/{dept}/heads/{head_id}/revoke",
                headers=o_hdr,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/departments/{dept}", headers=h_hdr
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/sections/{sec}/coordinators/{coord_id}/revoke",
                headers=o_hdr,
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/sections/{sec}", headers=c_hdr
            ).status_code
            == 403
        )


def test_guardian_verification_consent_activation_revocation_and_disclosure(
    env46: Step46Env,
) -> None:
    owner_id, email_o = _user(env46, "Owner")
    guardian_id, email_g = _user(env46, "Guardian")
    other_guardian_id, email_g2 = _user(env46, "Other Guardian")
    student_id, email_s = _user(env46, "Priya Student")

    school = bootstrap_institution(
        env46.admin_db.sessions, name="Guardian School", owner_user_id=owner_id
    )
    tid = school.tenant_id
    settings = _settings(env46)
    o_hdr = _token(env46, settings, email_o)

    with TestClient(create_app(settings, env46.app_db)) as client:
        for uid, role in [
            (guardian_id, "guardian"),
            (other_guardian_id, "guardian"),
            (student_id, "student"),
        ]:
            assert (
                client.post(
                    f"/api/v1/tenants/{tid}/memberships",
                    headers=o_hdr,
                    json={"user_id": str(uid)},
                ).status_code
                == 201
            )
            assert (
                client.post(
                    f"/api/v1/tenants/{tid}/memberships/{uid}/roles",
                    headers=o_hdr,
                    json={"role": role},
                ).status_code
                == 201
            )

        g_hdr = _token(env46, settings, email_g)
        g2_hdr = _token(env46, settings, email_g2)
        s_hdr = _token(env46, settings, email_s)

        # Client cannot fabricate consent/active via arbitrary extra fields
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links",
                headers=o_hdr,
                json={
                    "guardian_user_id": str(guardian_id),
                    "student_user_id": str(student_id),
                    "active": True,
                    "consent_at": "2026-01-01T00:00:00Z",
                },
            ).status_code
            == 422
        )

        # Create inactive guardian link
        created = client.post(
            f"/api/v1/tenants/{tid}/guardian-links",
            headers=o_hdr,
            json={
                "guardian_user_id": str(guardian_id),
                "student_user_id": str(student_id),
            },
        )
        assert created.status_code == 201
        assert created.json()["active"] is False
        assert created.json()["verified_at"] is None
        assert created.json()["consent_at"] is None

        # Verification and activation remain disabled until an authoritative,
        # age- and jurisdiction-aware verification workflow exists.
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/activate",
                headers=o_hdr,
            ).status_code
            == 403
        )
        # Guardian cannot read student summary while link inactive
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/students/{student_id}/summary", headers=g_hdr
            ).status_code
            == 403
        )

        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/verify",
                headers=o_hdr,
            ).status_code
            == 403
        )

        # Neither admin nor guardian can fabricate student consent
        for bad_hdr in (o_hdr, g_hdr):
            assert (
                client.post(
                    f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/consent",
                    headers=bad_hdr,
                ).status_code
                == 403
            )
        # Student consent can be withdrawn, but is not sufficient for activation.
        consented = client.post(
            f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/consent",
            headers=s_hdr,
        )
        assert consented.status_code == 200
        assert consented.json()["consent_at"] is not None

        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/activate",
                headers=o_hdr,
            ).status_code
            == 403
        )

        # Guardian cannot read student data while activation is deferred.
        summary = client.get(
            f"/api/v1/tenants/{tid}/students/{student_id}/summary", headers=g_hdr
        )
        assert summary.status_code == 403

        # Owner/admin and unrelated guardian cannot read student summary
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/students/{student_id}/summary", headers=o_hdr
            ).status_code
            == 403
        )
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/students/{student_id}/summary", headers=g2_hdr
            ).status_code
            == 403
        )

        # Scoped list shows guardian only their own links
        assert (
            len(
                client.get(
                    f"/api/v1/tenants/{tid}/guardian-links", headers=g_hdr
                ).json()["items"]
            )
            == 1
        )
        assert (
            client.get(f"/api/v1/tenants/{tid}/guardian-links", headers=g2_hdr).json()[
                "items"
            ]
            == []
        )

        # Unrelated guardian cannot revoke guardian_id's link
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/revoke",
                headers=g2_hdr,
            ).status_code
            == 403
        )

        # Student revokes/withdraws consent -> guardian immediately loses access and re-activation requires fresh consent
        revoked = client.post(
            f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/revoke",
            headers=s_hdr,
        )
        assert revoked.status_code == 200
        assert revoked.json()["active"] is False
        assert revoked.json()["consent_at"] is None
        assert (
            client.get(
                f"/api/v1/tenants/{tid}/students/{student_id}/summary", headers=g_hdr
            ).status_code
            == 403
        )
        assert (
            client.post(
                f"/api/v1/tenants/{tid}/guardian-links/{guardian_id}/students/{student_id}/activate",
                headers=o_hdr,
            ).status_code
            == 403
        )

        # Audit persistence check
        with env46.admin.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM security_events WHERE tenant_id=:t AND event_kind='change' AND action LIKE 'guardian_link.%'"
                    ),
                    {"t": tid},
                )
                >= 3
            )
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM security_events WHERE tenant_id=:t AND NOT allowed"
                    ),
                    {"t": tid},
                )
                >= 5
            )
