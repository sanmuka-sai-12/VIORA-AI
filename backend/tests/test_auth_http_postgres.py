"""HTTP authentication integration and security regression tests against live PostgreSQL."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, delete, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.schema import CreateSchema, DropSchema
from viora.config import AuthSettings, Settings
from viora.db import Database
from viora.identity.models import AuthRateLimit, User
from viora.identity.provision import main as provision_cli
from viora.identity.public import AccountStatus, AuthService, provision_account
from viora.identity.router import DEV_REFRESH_COOKIE, PROD_REFRESH_COOKIE
from viora.main import create_app

pytestmark = pytest.mark.postgres

TRUSTED_ORIGIN = "https://app.viora.example"
BASE_URL = "https://api.viora.example"
PASSWORD = SecretStr("correct horse battery staple 42")
NEW_PASSWORD = SecretStr("another strong passphrase 99")


@pytest.fixture(scope="module")
def pg_database() -> Iterator[tuple[Database, str]]:
    raw_url = os.environ.get("VIORA_TEST_DATABASE_URL")
    if not raw_url:
        pytest.skip("Requires dedicated PostgreSQL integration database")
    schema = f"auth_http_{uuid4().hex}"
    url = make_url(raw_url)
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    scoped_str = scoped_url.render_as_string(hide_password=False)
    admin = create_engine(url)
    db = Database(Settings(database_url=SecretStr(scoped_str)))
    with admin.begin() as connection:
        connection.execute(CreateSchema(schema))
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATABASE_URL", scoped_str)
            command.upgrade(
                Config(str(Path(__file__).resolve().parents[2] / "alembic.ini")),
                "head",
            )
        yield db, scoped_str
    finally:
        db.close()
        with admin.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        admin.dispose()


@dataclass
class HttpHarness:
    db: Database
    db_url: str
    email: str
    user_id: object

    def make_settings(
        self,
        *,
        environment: str = "test",
        cookie_secure: bool = True,
        login_rate_limit: int = 10,
        refresh_rate_limit: int = 30,
    ) -> Settings:
        return Settings(
            database_url=SecretStr(self.db_url),
            environment=environment,  # type: ignore[arg-type]
            cors_origins=(TRUSTED_ORIGIN,),
            auth=AuthSettings(
                signing_key=SecretStr("k" * 64),
                cookie_secure=cookie_secure,
                login_rate_limit=login_rate_limit,
                refresh_rate_limit=refresh_rate_limit,
            ),
        )


@pytest.fixture
def harness(pg_database: tuple[Database, str]) -> HttpHarness:
    db, db_url = pg_database
    with db.sessions.begin() as session:
        session.execute(delete(AuthRateLimit))
    email = f"user-{uuid4()}@example.test"
    summary = provision_account(
        db.sessions,
        email=email,
        display_name="Test Learner",
        password=PASSWORD,
    )
    return HttpHarness(db=db, db_url=db_url, email=email, user_id=summary.user_id)


def _auth_headers(token: str | None = None) -> dict[str, str]:
    headers = {"Origin": TRUSTED_ORIGIN, "X-Viora-CSRF": "1"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def test_login_me_refresh_rotation_and_replay_revocation(harness: HttpHarness) -> None:
    app = create_app(harness.make_settings(), harness.db)
    with TestClient(app, base_url=BASE_URL) as client:
        # Unsuccessful login returns generic 401 and no-store
        bad = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": "wrong password 123"},
        )
        assert bad.status_code == 401
        assert bad.headers["Cache-Control"] == "no-store"
        assert bad.headers["WWW-Authenticate"] == "Bearer"
        assert bad.json()["code"] == "unauthenticated"

        # Unknown user returns identical generic 401
        unknown = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": "nobody@example.test", "password": "wrong password 123"},
        )
        assert unknown.status_code == 401
        assert unknown.json()["message"] == bad.json()["message"]

        # Successful login
        ok = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={
                "identifier": harness.email.upper(),
                "password": PASSWORD.get_secret_value(),
            },
        )
        assert ok.status_code == 200
        assert ok.headers["Cache-Control"] == "no-store"
        body = ok.json()
        assert body["token_type"] == "Bearer"
        assert "refresh_token" not in body
        assert "password" not in ok.text
        assert PASSWORD.get_secret_value() not in ok.text

        # Cookie security attributes
        set_cookie = ok.headers["set-cookie"]
        assert f"{PROD_REFRESH_COOKIE}=" in set_cookie
        lower_cookie = set_cookie.lower()
        assert "httponly" in lower_cookie
        assert "secure" in lower_cookie
        assert "samesite=strict" in lower_cookie
        assert "path=/" in lower_cookie
        assert "domain=" not in lower_cookie
        assert "max-age=" in lower_cookie

        access_1 = body["access_token"]
        cookie_1 = client.cookies.get(PROD_REFRESH_COOKIE)
        assert cookie_1 is not None

        # GET /me verifies access token
        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_1}"},
        )
        assert me.status_code == 200
        assert me.headers["Cache-Control"] == "no-store"
        assert me.json()["user_id"] == str(harness.user_id)
        assert me.json()["display_name"] == "Test Learner"
        assert me.json()["session_id"] == body["session_id"]

        # Access interface wired: protected source route rejects unauthenticated with 401,
        # and authenticated with 403 deny-by-default until Step 4.3
        unauth_source = client.get(f"/api/v1/tenants/{uuid4()}/sources/{uuid4()}")
        assert unauth_source.status_code == 401
        denied_source = client.get(
            f"/api/v1/tenants/{uuid4()}/sources/{uuid4()}",
            headers={"Authorization": f"Bearer {access_1}"},
        )
        assert denied_source.status_code == 403

        # Refresh rotates cookie and issues new access token
        refreshed = client.post("/api/v1/auth/refresh", headers=_auth_headers())
        assert refreshed.status_code == 200
        assert refreshed.headers["Cache-Control"] == "no-store"
        refreshed_body = refreshed.json()
        assert "refresh_token" not in refreshed_body
        cookie_2 = client.cookies.get(PROD_REFRESH_COOKIE)
        assert cookie_2 is not None and cookie_2 != cookie_1
        access_2 = refreshed_body["access_token"]

        # Replay of old cookie_1 is rejected and revokes the entire session family
        client.cookies.clear()
        client.cookies.set(PROD_REFRESH_COOKIE, cookie_1)
        replay = client.post("/api/v1/auth/refresh", headers=_auth_headers())
        assert replay.status_code == 401
        assert replay.headers["Cache-Control"] == "no-store"
        assert f"{PROD_REFRESH_COOKIE}=\"\"" in replay.headers["set-cookie"] or "max-age=0" in replay.headers["set-cookie"].lower()

        # Even winner cookie_2 and access_2 are now revoked
        client.cookies.clear()
        client.cookies.set(PROD_REFRESH_COOKIE, cookie_2)
        winner_after_replay = client.post("/api/v1/auth/refresh", headers=_auth_headers())
        assert winner_after_replay.status_code == 401
        me_after_replay = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access_2}"},
        )
        assert me_after_replay.status_code == 401


def test_logout_logout_all_and_password_change(harness: HttpHarness) -> None:
    app = create_app(harness.make_settings(), harness.db)
    with TestClient(app, base_url=BASE_URL) as client:
        # Login session 1
        s1 = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        ).json()
        # Logout current session
        out = client.post("/api/v1/auth/logout", headers=_auth_headers(s1["access_token"]))
        assert out.status_code == 204
        assert out.headers["Cache-Control"] == "no-store"
        assert "max-age=0" in out.headers["set-cookie"].lower()
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {s1['access_token']}"},
            ).status_code
            == 401
        )

        # Login two sessions and logout-all
        s2 = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        ).json()
        s3 = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        ).json()
        out_all = client.post(
            "/api/v1/auth/logout-all", headers=_auth_headers(s2["access_token"])
        )
        assert out_all.status_code == 204
        assert "max-age=0" in out_all.headers["set-cookie"].lower()
        for tok in (s2["access_token"], s3["access_token"]):
            assert (
                client.get(
                    "/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"}
                ).status_code
                == 401
            )

        # Login again and change password
        s4 = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        ).json()
        short_pw = client.post(
            "/api/v1/auth/password/change",
            headers=_auth_headers(s4["access_token"]),
            json={
                "current_password": PASSWORD.get_secret_value(),
                "new_password": "short",
            },
        )
        assert short_pw.status_code == 422

        wrong_pw = client.post(
            "/api/v1/auth/password/change",
            headers=_auth_headers(s4["access_token"]),
            json={
                "current_password": "wrong current password",
                "new_password": NEW_PASSWORD.get_secret_value(),
            },
        )
        assert wrong_pw.status_code == 401

        # Re-login since wrong_pw didn't revoke s4 (s4 is still valid; test valid change)
        changed = client.post(
            "/api/v1/auth/password/change",
            headers=_auth_headers(s4["access_token"]),
            json={
                "current_password": PASSWORD.get_secret_value(),
                "new_password": NEW_PASSWORD.get_secret_value(),
            },
        )
        assert changed.status_code == 204
        assert changed.headers["Cache-Control"] == "no-store"
        assert "max-age=0" in changed.headers["set-cookie"].lower()

        # Current session is revoked after password change
        assert (
            client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {s4['access_token']}"},
            ).status_code
            == 401
        )
        # Old password rejected, new password accepted
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=_auth_headers(),
                json={
                    "identifier": harness.email,
                    "password": PASSWORD.get_secret_value(),
                },
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=_auth_headers(),
                json={
                    "identifier": harness.email,
                    "password": NEW_PASSWORD.get_secret_value(),
                },
            ).status_code
            == 200
        )


def test_csrf_origin_cors_content_type_and_body_limits(harness: HttpHarness) -> None:
    app = create_app(harness.make_settings(), harness.db)
    with TestClient(app, base_url=BASE_URL) as client:
        # Missing or invalid CSRF header -> 403
        for csrf_headers in [
            {"Origin": TRUSTED_ORIGIN},
            {"Origin": TRUSTED_ORIGIN, "X-Viora-CSRF": "0"},
        ]:
            resp = client.post(
                "/api/v1/auth/login",
                headers=csrf_headers,
                json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "csrf_required"
            assert resp.headers["Cache-Control"] == "no-store"

        # Untrusted, null, or missing Origin -> 403
        for origin in [None, "null", "https://evil.example"]:
            headers = {"X-Viora-CSRF": "1"}
            if origin is not None:
                headers["Origin"] = origin
            resp = client.post(
                "/api/v1/auth/login",
                headers=headers,
                json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
            )
            assert resp.status_code == 403
            assert resp.json()["code"] == "untrusted_origin"
            assert "access-control-allow-origin" not in resp.headers

        # Credentialed CORS preflight allows only exact trusted origin & headers
        preflight = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": TRUSTED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-Viora-CSRF",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == TRUSTED_ORIGIN
        assert preflight.headers["access-control-allow-credentials"] == "true"

        # Form-encoded / text content-type rejected with 415
        form_resp = client.post(
            "/api/v1/auth/login",
            headers={**_auth_headers(), "Content-Type": "application/x-www-form-urlencoded"},
            content=b"identifier=a&password=b",
        )
        assert form_resp.status_code == 415

        # Oversized body rejected with 413
        big_resp = client.post(
            "/api/v1/auth/login",
            headers={**_auth_headers(), "Content-Type": "application/json"},
            content=b'{"identifier":"a","password":"' + b"x" * 20000 + b'"}',
        )
        assert big_resp.status_code == 413

        # Refresh rejects query parameters or JSON body
        assert (
            client.post(
                "/api/v1/auth/refresh?refresh_token=abc", headers=_auth_headers()
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/v1/auth/refresh",
                headers=_auth_headers(),
                json={"refresh_token": "abc"},
            ).status_code
            == 422
        )


def test_rate_limiting_across_workers_and_disabled_account(
    harness: HttpHarness,
) -> None:
    # Two separate FastAPI app instances simulating separate worker processes
    worker_a = create_app(harness.make_settings(login_rate_limit=2), harness.db)
    worker_b = create_app(harness.make_settings(login_rate_limit=2), harness.db)
    with (
        TestClient(worker_a, base_url=BASE_URL) as client_a,
        TestClient(worker_b, base_url=BASE_URL) as client_b,
    ):
        assert (
            client_a.post(
                "/api/v1/auth/login",
                headers=_auth_headers(),
                json={"identifier": harness.email, "password": "wrong password 1"},
            ).status_code
            == 401
        )
        assert (
            client_b.post(
                "/api/v1/auth/login",
                headers=_auth_headers(),
                json={"identifier": harness.email, "password": "wrong password 2"},
            ).status_code
            == 401
        )
        limited = client_a.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        )
        assert limited.status_code == 429
        assert limited.headers["Cache-Control"] == "no-store"
        assert int(limited.headers["Retry-After"]) >= 1

    # Disabled account test using a high rate limit app
    app = create_app(harness.make_settings(login_rate_limit=50), harness.db)
    with TestClient(app, base_url=BASE_URL) as client:
        logged = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        )
        assert logged.status_code == 200
        token = logged.json()["access_token"]

        with harness.db.sessions.begin() as session:
            session.execute(
                update(User)
                .where(User.user_id == harness.user_id)
                .values(status=AccountStatus.disabled)
            )

        assert (
            client.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
            ).status_code
            == 401
        )
        assert client.post("/api/v1/auth/refresh", headers=_auth_headers()).status_code == 401
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=_auth_headers(),
                json={
                    "identifier": harness.email,
                    "password": PASSWORD.get_secret_value(),
                },
            ).status_code
            == 401
        )


def test_server_failure_preserves_refresh_cookie(harness: HttpHarness) -> None:
    app = create_app(harness.make_settings(refresh_rate_limit=50), harness.db)
    with TestClient(app, base_url=BASE_URL, raise_server_exceptions=False) as client:
        logged = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        )
        assert logged.status_code == 200
        assert client.cookies.get(PROD_REFRESH_COOKIE) is not None

        with patch.object(
            AuthService,
            "refresh",
            side_effect=OperationalError("stmt", {}, Exception("db down")),
        ):
            failed = client.post("/api/v1/auth/refresh", headers=_auth_headers())
        assert failed.status_code == 500
        assert failed.headers["Cache-Control"] == "no-store"
        assert "set-cookie" not in failed.headers
        assert client.cookies.get(PROD_REFRESH_COOKIE) is not None


def test_cookie_dev_vs_prod_and_provisioning_guard(harness: HttpHarness) -> None:
    with pytest.raises(ValueError, match="Production requires Secure"):
        harness.make_settings(environment="production", cookie_secure=False)

    dev_app = create_app(
        harness.make_settings(environment="development", cookie_secure=False),
        harness.db,
    )
    with TestClient(dev_app, base_url="http://localhost") as client:
        resp = client.post(
            "/api/v1/auth/login",
            headers=_auth_headers(),
            json={"identifier": harness.email, "password": PASSWORD.get_secret_value()},
        )
        assert resp.status_code == 200
        assert f"{DEV_REFRESH_COOKIE}=" in resp.headers["set-cookie"]
        assert "__Host-" not in resp.headers["set-cookie"]

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_URL", harness.db_url)
        mp.setenv("APP_ENV", "production")
        mp.setenv("VIORA_PROVISION_PASSWORD", PASSWORD.get_secret_value())
        assert (
            provision_cli(
                ["--email", f"prod-{uuid4()}@example.test", "--display-name", "Nope"]
            )
            == 1
        )
