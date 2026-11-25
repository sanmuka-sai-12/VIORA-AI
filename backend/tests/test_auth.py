from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr, ValidationError
from viora.identity.auth import AuthFailure, AuthSettings, Passwords, Tokens


@pytest.fixture
def settings() -> AuthSettings:
    return AuthSettings(signing_key=SecretStr("s" * 64))


def test_passwords() -> None:
    passwords = Passwords()
    encoded = passwords.hash(SecretStr("correct password"))
    assert encoded.startswith("$argon2id$")
    assert passwords.verify(SecretStr("correct password"), encoded)
    assert not passwords.verify(SecretStr("wrong"), encoded)
    assert not passwords.verify(SecretStr("wrong"), None)
    assert not passwords.verify(SecretStr("wrong"), "broken")


def test_token_round_trip(settings: AuthSettings) -> None:
    tokens = Tokens(settings)
    actor, session = uuid4(), uuid4()
    encoded = tokens.issue(actor, session)
    assert tokens.decode(encoded) == (actor, session)
    claims = jwt.decode(encoded, options={"verify_signature": False})
    assert claims["exp"] - claims["iat"] == 900
    assert set(claims) == {"sub", "sid", "iss", "aud", "iat", "exp"}


@pytest.mark.parametrize(
    "mutation",
    [
        "algorithm",
        "signature",
        "issuer",
        "audience",
        "expiry",
        "actor",
        "session",
        "missing",
        "type",
    ],
)
def test_invalid_tokens(settings: AuthSettings, mutation: str) -> None:
    tokens = Tokens(settings)
    claims = jwt.decode(
        tokens.issue(uuid4(), uuid4()), options={"verify_signature": False}
    )
    algorithm, key = "HS256", settings.signing_key.get_secret_value()
    if mutation == "algorithm":
        algorithm = "HS384"
    elif mutation == "signature":
        key = "x" * 64
    elif mutation == "issuer":
        claims["iss"] = "other"
    elif mutation == "audience":
        claims["aud"] = "other"
    elif mutation == "expiry":
        claims["exp"] = int((datetime.now(UTC) - timedelta(seconds=1)).timestamp())
    elif mutation == "actor":
        claims["sub"] = "invalid"
    elif mutation == "session":
        claims["sid"] = "invalid"
    elif mutation == "missing":
        del claims["sid"]
    else:
        claims["exp"] = str(claims["exp"])
    with pytest.raises(AuthFailure):
        tokens.decode(jwt.encode(claims, key, algorithm=algorithm))


def test_weak_key_rejected() -> None:
    with pytest.raises(ValidationError):
        AuthSettings(signing_key=SecretStr("short"))


def test_rehash_preserves_verified_legacy_password() -> None:
    from pwdlib.hashers.argon2 import Argon2Hasher

    passwords = Passwords()
    password = SecretStr("legacy")
    old = Argon2Hasher(time_cost=1).hash(password.get_secret_value())
    upgraded = passwords.rehash(password, old)
    assert upgraded is not None and passwords.verify(password, upgraded)
    assert passwords.rehash(password, upgraded) is None


def test_service_rejects_caller_transaction(settings: AuthSettings) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from viora.identity.public import AuthService

    engine = create_engine("sqlite://")
    try:
        with (
            engine.connect() as connection,
            pytest.raises(ValueError, match="engine-bound PostgreSQL"),
        ):
            AuthService(sessionmaker(connection), settings)
        with pytest.raises(ValueError, match="engine-bound PostgreSQL"):
            AuthService(sessionmaker(engine), settings)
    finally:
        engine.dispose()


def test_secret_contract_redacts_tokens() -> None:
    from viora.identity.public import TokenPair

    pair = TokenPair(
        access_token=SecretStr("access secret"),
        refresh_token=SecretStr("refresh secret"),
        session_id=uuid4(),
        access_expires_in=900,
        refresh_expires_at=datetime.now(UTC),
        session_expires_at=datetime.now(UTC),
    )
    for output in (repr(pair), pair.model_dump_json()):
        assert "access secret" not in output and "refresh secret" not in output


@pytest.mark.parametrize("claim", ["sub", "sid", "iss", "aud", "iat", "exp"])
def test_required_claims(settings: AuthSettings, claim: str) -> None:
    tokens = Tokens(settings)
    payload = jwt.decode(
        tokens.issue(uuid4(), uuid4()), options={"verify_signature": False}
    )
    del payload[claim]
    with pytest.raises(AuthFailure):
        tokens.decode(
            jwt.encode(
                payload, settings.signing_key.get_secret_value(), algorithm="HS256"
            )
        )


@pytest.mark.parametrize("token", ["", "not.a.jwt", "x" * 4097])
def test_malformed_tokens(settings: AuthSettings, token: str) -> None:
    with pytest.raises(AuthFailure):
        Tokens(settings).decode(token)


def test_lifetime_configuration() -> None:
    with pytest.raises(ValidationError):
        AuthSettings(
            signing_key=SecretStr("s" * 64), refresh_seconds=100, absolute_seconds=99
        )
    with pytest.raises(ValidationError):
        AuthSettings(signing_key=SecretStr("s" * 64), access_seconds=0)
