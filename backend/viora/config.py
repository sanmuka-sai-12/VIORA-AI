import os
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.engine import make_url

DEFAULT_ACCESS_SECONDS = 15 * 60
DEFAULT_REFRESH_SECONDS = 7 * 24 * 60 * 60
DEFAULT_ABSOLUTE_SECONDS = 30 * 24 * 60 * 60


class AuthSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    signing_key: SecretStr
    issuer: str = Field(default="viora", min_length=1)
    audience: str = Field(default="viora-api", min_length=1)
    access_seconds: int = Field(default=DEFAULT_ACCESS_SECONDS, gt=0)
    refresh_seconds: int = Field(default=DEFAULT_REFRESH_SECONDS, gt=0)
    absolute_seconds: int = Field(default=DEFAULT_ABSOLUTE_SECONDS, gt=0)
    cookie_secure: bool = True
    login_rate_limit: int = Field(default=10, gt=0)
    login_rate_window_seconds: int = Field(default=60, gt=0)
    refresh_rate_limit: int = Field(default=30, gt=0)
    refresh_rate_window_seconds: int = Field(default=60, gt=0)

    @model_validator(mode="after")
    def validate_security(self) -> Self:
        if len(self.signing_key.get_secret_value().encode()) < 32:
            raise ValueError(
                "JWT signing key requires at least 32 bytes of random secret material"
            )
        if self.refresh_seconds > self.absolute_seconds:
            raise ValueError("Refresh lifetime cannot exceed absolute session lifetime")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    auth: AuthSettings | None = None
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    cors_origins: tuple[str, ...] = ()
    pool_size: int = Field(default=5, gt=0)
    pool_timeout: int = Field(default=5, gt=0)
    media_storage_dir: str = Field(default="./storage", min_length=1)
    openai_api_key: SecretStr | None = None
    openai_base_url: str | None = None
    trusted_ai_hosts: tuple[str, ...] = (
        "api.openai.com",
        "api.cloudflare.com",
        "gateway.ai.cloudflare.com",
    )
    allow_local_ai_endpoint: bool = False
    default_ai_model: str = "gpt-4o-mini"
    default_embedding_model: str = "text-embedding-3-small"
    source_max_bytes: int = Field(
        default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024
    )
    source_max_pdf_pages: int = Field(default=100, ge=1, le=500)
    source_max_text_chars: int = Field(default=200_000, ge=10, le=1_000_000)
    source_min_text_chars: int = Field(default=3, ge=1, le=100)

    @model_validator(mode="after")
    def validate_connections(self) -> Self:
        from sqlalchemy.exc import ArgumentError

        try:
            url = make_url(self.database_url.get_secret_value())
        except ArgumentError:
            raise ValueError("Invalid database URL") from None
        if url.drivername != "postgresql+psycopg" or not url.database:
            raise ValueError("A PostgreSQL psycopg database is required")
        for origin in self.cors_origins:
            parts = urlsplit(origin)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or "*" in origin
                or parts.username
                or parts.password
                or parts.path
                or parts.query
                or parts.fragment
            ):
                raise ValueError("Expected an explicit HTTP origin")
            _ = parts.port
        if (
            self.environment == "production"
            and self.auth is not None
            and not self.auth.cookie_secure
        ):
            raise ValueError("Production requires Secure authentication cookies")
        if self.environment == "production" and self.allow_local_ai_endpoint:
            raise ValueError(
                "Local insecure AI endpoints cannot be enabled in production"
            )
        return self

    @classmethod
    def load(cls) -> Self:
        values = {
            **dotenv_values(Path(__file__).resolve().parents[2] / ".env"),
            **os.environ,
        }
        return cls.model_validate(
            {
                "database_url": values.get("DATABASE_URL"),
                "auth": {
                    "signing_key": values.get("AUTH_SIGNING_KEY"),
                    "issuer": values.get("AUTH_ISSUER", "viora"),
                    "audience": values.get("AUTH_AUDIENCE", "viora-api"),
                    "access_seconds": values.get(
                        "AUTH_ACCESS_SECONDS", DEFAULT_ACCESS_SECONDS
                    ),
                    "refresh_seconds": values.get(
                        "AUTH_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS
                    ),
                    "absolute_seconds": values.get(
                        "AUTH_ABSOLUTE_SECONDS", DEFAULT_ABSOLUTE_SECONDS
                    ),
                    "cookie_secure": values.get("AUTH_COOKIE_SECURE", "true"),
                    "login_rate_limit": values.get("AUTH_LOGIN_RATE_LIMIT", 10),
                    "login_rate_window_seconds": values.get(
                        "AUTH_LOGIN_RATE_WINDOW_SECONDS", 60
                    ),
                    "refresh_rate_limit": values.get("AUTH_REFRESH_RATE_LIMIT", 30),
                    "refresh_rate_window_seconds": values.get(
                        "AUTH_REFRESH_RATE_WINDOW_SECONDS", 60
                    ),
                }
                if values.get("AUTH_SIGNING_KEY")
                else None,
                "environment": values.get("APP_ENV", "development"),
                "cors_origins": tuple(
                    x.strip()
                    for x in (values.get("CORS_ORIGINS") or "").split(",")
                    if x.strip()
                ),
                "pool_size": values.get("DB_POOL_SIZE", 5),
                "pool_timeout": values.get("DB_POOL_TIMEOUT", 5),
                "media_storage_dir": values.get("MEDIA_STORAGE_DIR", "./storage"),
                "openai_api_key": values.get("OPENAI_API_KEY"),
                "openai_base_url": values.get("OPENAI_BASE_URL"),
                "trusted_ai_hosts": tuple(
                    x.strip().lower()
                    for x in (
                        values.get("TRUSTED_AI_HOSTS")
                        or "api.openai.com,api.cloudflare.com,gateway.ai.cloudflare.com"
                    ).split(",")
                    if x.strip()
                ),
                "allow_local_ai_endpoint": values.get(
                    "ALLOW_LOCAL_AI_ENDPOINT", "false"
                ),
                "default_ai_model": values.get("DEFAULT_AI_MODEL", "gpt-4o-mini"),
                "default_embedding_model": values.get(
                    "DEFAULT_EMBEDDING_MODEL", "text-embedding-3-small"
                ),
                "source_max_bytes": values.get("SOURCE_MAX_BYTES", 10 * 1024 * 1024),
                "source_max_pdf_pages": values.get("SOURCE_MAX_PDF_PAGES", 100),
                "source_max_text_chars": values.get("SOURCE_MAX_TEXT_CHARS", 200_000),
                "source_min_text_chars": values.get("SOURCE_MIN_TEXT_CHARS", 3),
            }
        )
