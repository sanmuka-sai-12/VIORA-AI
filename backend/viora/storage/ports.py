from collections.abc import Iterable, Iterator
from typing import Protocol
from uuid import UUID

from ..contracts import ArtifactVersionRef, TenantContext
from .schemas import DEFAULT_CHUNK_BYTES, ArtifactMetadata, StoredArtifact


class ArtifactStore(Protocol):
    """Provider-independent immutable tenant-scoped binary artifact store."""

    def put_bytes(
        self,
        context: TenantContext,
        data: bytes,
        *,
        kind: str,
        resource_id: UUID,
        content_type: str = "application/octet-stream",
        artifact_id: UUID | None = None,
        version_id: UUID | None = None,
        max_bytes: int | None = None,
    ) -> StoredArtifact: ...

    def put_stream(
        self,
        context: TenantContext,
        chunks: Iterable[bytes],
        *,
        kind: str,
        resource_id: UUID,
        content_type: str = "application/octet-stream",
        artifact_id: UUID | None = None,
        version_id: UUID | None = None,
        max_bytes: int | None = None,
    ) -> StoredArtifact: ...

    def read(self, context: TenantContext, artifact: ArtifactVersionRef) -> bytes: ...

    def stream(
        self,
        context: TenantContext,
        artifact: ArtifactVersionRef,
        *,
        chunk_size: int = DEFAULT_CHUNK_BYTES,
        max_bytes: int | None = None,
    ) -> Iterator[bytes]:
        """Yield artifact chunks; SHA-256 verification completes at EOF."""
        ...

    def delete_uncommitted(
        self, context: TenantContext, artifact: ArtifactVersionRef
    ) -> bool:
        """Compensating cleanup ONLY for uncommitted artifacts whose DB transaction failed."""
        ...

    def inspect(
        self, context: TenantContext, artifact: ArtifactVersionRef
    ) -> ArtifactMetadata: ...

    def exists(self, context: TenantContext, artifact: ArtifactVersionRef) -> bool: ...
