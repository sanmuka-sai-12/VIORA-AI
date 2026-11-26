import re
from datetime import datetime
from uuid import UUID

from pydantic import Field

from ..contracts import ArtifactVersionRef, Contract

SAFE_KIND_RE = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
SAFE_CONTENT_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,63}/[a-z0-9][a-z0-9!#$&^_.+-]{0,127}$"
)

DEFAULT_MAX_ARTIFACT_BYTES = 50 * 1024 * 1024  # 50 MiB
DEFAULT_CHUNK_BYTES = 64 * 1024  # 64 KiB


class ArtifactMetadata(Contract):
    ref: ArtifactVersionRef
    content_type: str = Field(min_length=3, max_length=192)
    byte_size: int = Field(ge=0)
    created_at: datetime


class StoredArtifact(Contract):
    metadata: ArtifactMetadata
    storage_key: str


class StorageError(Exception):
    """Base exception for private artifact storage operations."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StorageConfigurationError(StorageError):
    def __init__(self, message: str = "Invalid storage configuration") -> None:
        super().__init__("storage_configuration_error", message)


class StorageSecurityError(StorageError):
    def __init__(self, message: str = "Unsafe storage path or symlink rejected") -> None:
        super().__init__("storage_security_error", message)


class StorageAuthorizationError(StorageError):
    def __init__(
        self, message: str = "Tenant context does not match target artifact"
    ) -> None:
        super().__init__("storage_tenant_mismatch", message)


class ArtifactMissingError(StorageError):
    def __init__(self, message: str = "Requested artifact does not exist") -> None:
        super().__init__("artifact_missing", message)


class ArtifactExistsError(StorageError):
    def __init__(
        self, message: str = "Artifact version already exists and is immutable"
    ) -> None:
        super().__init__("artifact_exists", message)


class ArtifactCorruptedError(StorageError):
    def __init__(
        self, message: str = "Artifact failed SHA-256 integrity verification"
    ) -> None:
        super().__init__("artifact_corrupted", message)


class ArtifactOversizedError(StorageError):
    def __init__(
        self, message: str = "Artifact exceeds maximum permitted byte limit"
    ) -> None:
        super().__init__("artifact_oversized", message)


def validate_content_type(content_type: str) -> str:
    cleaned = content_type.split(";", 1)[0].strip().lower()
    if not SAFE_CONTENT_TYPE_RE.match(cleaned):
        raise StorageError("invalid_content_type", "Unsupported or unsafe content type")
    return cleaned


def validate_artifact_kind(kind: str) -> str:
    if not SAFE_KIND_RE.match(kind):
        raise StorageError("invalid_artifact_kind", "Invalid artifact kind identifier")
    return kind


def format_tenant_storage_key(
    tenant_id: UUID, artifact_id: UUID, version_id: UUID
) -> str:
    """Canonical server-generated object key compatible with S3-like keyspaces."""
    return f"tenants/{tenant_id}/{artifact_id}/{version_id}"
