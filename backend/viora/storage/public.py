from pathlib import Path

from .local_adapter import LocalFilesystemArtifactStore, resolve_safe_subpath
from .ports import ArtifactStore
from .schemas import (
    DEFAULT_CHUNK_BYTES,
    DEFAULT_MAX_ARTIFACT_BYTES,
    ArtifactCorruptedError,
    ArtifactExistsError,
    ArtifactMetadata,
    ArtifactMissingError,
    ArtifactOversizedError,
    StorageAuthorizationError,
    StorageConfigurationError,
    StorageError,
    StorageSecurityError,
    StoredArtifact,
    format_tenant_storage_key,
)


def local_artifact_store(
    root_dir: str | Path,
    *,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> ArtifactStore:
    return LocalFilesystemArtifactStore(root_dir, max_artifact_bytes=max_artifact_bytes)


__all__ = [
    "DEFAULT_CHUNK_BYTES",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "ArtifactCorruptedError",
    "ArtifactExistsError",
    "ArtifactMetadata",
    "ArtifactMissingError",
    "ArtifactOversizedError",
    "ArtifactStore",
    "LocalFilesystemArtifactStore",
    "StorageAuthorizationError",
    "StorageConfigurationError",
    "StorageError",
    "StorageSecurityError",
    "StoredArtifact",
    "format_tenant_storage_key",
    "local_artifact_store",
    "resolve_safe_subpath",
]
