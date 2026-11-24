import os
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from sqlalchemy.engine import make_url


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    database_url: SecretStr
    environment: Literal["development", "test", "production"] = "development"
    cors_origins: tuple[str, ...] = ()
    pool_size: int = Field(default=5, gt=0)
    pool_timeout: int = Field(default=5, gt=0)

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
                "environment": values.get("APP_ENV", "development"),
                "cors_origins": tuple(
                    x.strip()
                    for x in (values.get("CORS_ORIGINS") or "").split(",")
                    if x.strip()
                ),
                "pool_size": values.get("DB_POOL_SIZE", 5),
                "pool_timeout": values.get("DB_POOL_TIMEOUT", 5),
            }
        )
