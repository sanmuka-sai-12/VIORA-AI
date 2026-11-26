import hashlib
import json
import os
import stat
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ..contracts import ArtifactVersionRef, TenantContext
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
    StorageSecurityError,
    StoredArtifact,
    format_tenant_storage_key,
    validate_artifact_kind,
    validate_content_type,
)

O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
O_BINARY = getattr(os, "O_BINARY", 0)
_IS_JUNCTION = getattr(os.path, "isjunction", lambda _p: False)
_REPARSE_ATTR = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _reject_symlink(path: Path) -> None:
    if path.is_symlink() or _IS_JUNCTION(path):
        raise StorageSecurityError(
            "Symlinks and junctions are prohibited inside private storage"
        )
    if _REPARSE_ATTR:
        try:
            attrs = getattr(os.lstat(path), "st_file_attributes", 0)
        except OSError:
            attrs = 0
        if attrs & _REPARSE_ATTR:
            raise StorageSecurityError(
                "Filesystem reparse points are prohibited inside private storage"
            )


def _ensure_dir_no_symlink(root_real: Path, parts: tuple[str, ...]) -> Path:
    current = root_real
    _reject_symlink(current)
    for segment in parts:
        if (
            not segment
            or segment in {".", ".."}
            or "/" in segment
            or "\\" in segment
            or "\x00" in segment
        ):
            raise StorageSecurityError("Invalid path segment rejected")
        current = current / segment
        _reject_symlink(current)
        if current.exists():
            if not current.is_dir():
                raise StorageSecurityError("Expected directory in storage hierarchy")
        else:
            current.mkdir(mode=0o700, exist_ok=True)
            _reject_symlink(current)
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root_real):
        raise StorageSecurityError("Resolved storage directory escaped root")
    return resolved


def resolve_safe_subpath(root: Path, *segments: str) -> Path:
    """Validate arbitrary path segments cleanly against traversal and symlinks."""
    root_real = root.resolve(strict=True)
    _reject_symlink(root_real)
    current = root_real
    for segment in segments:
        if (
            not segment
            or segment in {".", ".."}
            or "/" in segment
            or "\\" in segment
            or ":" in segment
            or "\x00" in segment
        ):
            raise StorageSecurityError("Unsafe storage path segment rejected")
        current = current / segment
        _reject_symlink(current)
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(root_real):
        raise StorageSecurityError("Storage path traversal outside root rejected")
    return resolved


class LocalFilesystemArtifactStore(ArtifactStore):
    """Immutable, tenant-partitioned local filesystem binary artifact store.

    Security notes and documented platform limits:
    - POSIX directories use 0o700 and artifact files use 0o600; on Windows NTFS,
      operators must restrict the parent storage directory ACL to the dedicated
      application service account because POSIX mode bits do not govern NTFS ACLs.
    - Every directory segment and target file is verified against symlinks,
      Windows NTFS junctions, and reparse points (plus O_NOFOLLOW where supported);
      however, user-space path checks cannot eliminate physical TOCTOU races if an
      unprivileged/host attacker already possesses write privileges inside the root.
    - Callers using ``stream()`` receive chunks incrementally and MUST NOT treat an
      artifact as verified until iteration reaches EOF without raising
      ``ArtifactCorruptedError``. Use ``read()`` or ``verify()`` for post-digest data.
    """

    def __init__(
        self,
        root_dir: str | Path,
        *,
        max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        if max_artifact_bytes <= 0:
            raise StorageConfigurationError("max_artifact_bytes must be positive")
        root_path = Path(root_dir)
        if root_path.is_symlink():
            raise StorageSecurityError("Storage root cannot be a symlink")
        root_path.mkdir(mode=0o700, parents=True, exist_ok=True)
        _reject_symlink(root_path)
        self.root = root_path.resolve(strict=True)
        self.max_artifact_bytes = max_artifact_bytes

    def _effective_max_bytes(self, max_bytes: int | None) -> int:
        if max_bytes is None:
            return self.max_artifact_bytes
        if max_bytes <= 0:
            raise StorageConfigurationError("max_bytes limit must be positive")
        return min(max_bytes, self.max_artifact_bytes)

    def _require_tenant_match(
        self, context: TenantContext, artifact: ArtifactVersionRef
    ) -> None:
        if context.tenant_id != artifact.tenant_id:
            raise StorageAuthorizationError(
                "Tenant context does not match target artifact"
            )

    def _existing_blob_path(self, artifact: ArtifactVersionRef) -> tuple[Path, Path]:
        blob_path = resolve_safe_subpath(
            self.root,
            "tenants",
            str(artifact.tenant_id),
            str(artifact.resource_id),
            f"{artifact.version_id}.blob",
        )
        meta_path = resolve_safe_subpath(
            self.root,
            "tenants",
            str(artifact.tenant_id),
            str(artifact.resource_id),
            f"{artifact.version_id}.meta.json",
        )
        return blob_path, meta_path

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
    ) -> StoredArtifact:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes-like")
        raw = bytes(data)
        limit = self._effective_max_bytes(max_bytes)
        if len(raw) > limit:
            raise ArtifactOversizedError(
                f"Artifact size {len(raw)} exceeds maximum permitted limit {limit}"
            )
        return self.put_stream(
            context,
            (raw[i : i + DEFAULT_CHUNK_BYTES] for i in range(0, len(raw), DEFAULT_CHUNK_BYTES))
            if raw
            else (b"",),
            kind=kind,
            resource_id=resource_id,
            content_type=content_type,
            artifact_id=artifact_id,
            version_id=version_id,
            max_bytes=limit,
        )

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
    ) -> StoredArtifact:
        validated_kind = validate_artifact_kind(kind)
        validated_ct = validate_content_type(content_type)
        limit = self._effective_max_bytes(max_bytes)
        # Keep resource_id aligned with artifact directory while preserving server-generated defaults.
        target_resource = artifact_id or resource_id
        target_version = version_id or uuid4()

        target_dir = _ensure_dir_no_symlink(
            self.root, ("tenants", str(context.tenant_id), str(target_resource))
        )
        target_blob = target_dir / f"{target_version}.blob"
        target_meta = target_dir / f"{target_version}.meta.json"
        if target_blob.exists() or target_blob.is_symlink():
            _reject_symlink(target_blob)
            raise ArtifactExistsError()

        tmp_suffix = uuid4().hex
        tmp_blob = target_dir / f".tmp-{tmp_suffix}.blob"
        tmp_meta = target_dir / f".tmp-{tmp_suffix}.meta"
        digest = hashlib.sha256()
        total_bytes = 0
        blob_fd: int | None = None
        meta_fd: int | None = None

        try:
            blob_fd = os.open(
                tmp_blob,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY,
                0o600,
            )
            for chunk in chunks:
                if not isinstance(chunk, (bytes, bytearray)):
                    raise ArtifactCorruptedError("Storage stream yielded non-bytes chunk")
                chunk_bytes = bytes(chunk)
                total_bytes += len(chunk_bytes)
                if total_bytes > limit:
                    raise ArtifactOversizedError(
                        f"Artifact stream exceeded maximum permitted limit {limit}"
                    )
                if chunk_bytes:
                    digest.update(chunk_bytes)
                    view = memoryview(chunk_bytes)
                    written = 0
                    while written < len(view):
                        n = os.write(blob_fd, view[written:])
                        if n <= 0:
                            raise OSError("Short write to private artifact store")
                        written += n
            os.fsync(blob_fd)
            os.close(blob_fd)
            blob_fd = None

            sha_hex = digest.hexdigest()
            created_at = datetime.now(UTC)
            ref = ArtifactVersionRef(
                tenant_id=context.tenant_id,
                kind=validated_kind,
                resource_id=target_resource,
                version_id=target_version,
                sha256=sha_hex,
            )
            metadata = ArtifactMetadata(
                ref=ref,
                content_type=validated_ct,
                byte_size=total_bytes,
                created_at=created_at,
            )

            meta_bytes = json.dumps(
                {
                    "tenant_id": str(context.tenant_id),
                    "kind": validated_kind,
                    "resource_id": str(target_resource),
                    "version_id": str(target_version),
                    "sha256": sha_hex,
                    "content_type": validated_ct,
                    "byte_size": total_bytes,
                    "created_at": created_at.isoformat(),
                },
                sort_keys=True,
            ).encode("utf-8")
            meta_fd = os.open(
                tmp_meta,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_NOFOLLOW | O_BINARY,
                0o600,
            )
            mview = memoryview(meta_bytes)
            mwritten = 0
            while mwritten < len(mview):
                n = os.write(meta_fd, mview[mwritten:])
                if n <= 0:
                    raise OSError("Short metadata write to private artifact store")
                mwritten += n
            os.fsync(meta_fd)
            os.close(meta_fd)
            meta_fd = None

            # Atomic exclusive promotion via hard link prevents concurrent overwrites.
            try:
                os.link(tmp_blob, target_blob)
            except FileExistsError:
                raise ArtifactExistsError() from None

            try:
                os.link(tmp_meta, target_meta)
            except FileExistsError:
                # Blob promotion is canonical; ignore benign metadata collision if already identical.
                pass

            storage_key = format_tenant_storage_key(
                context.tenant_id, target_resource, target_version
            )
            return StoredArtifact(metadata=metadata, storage_key=storage_key)
        finally:
            if blob_fd is not None:
                os.close(blob_fd)
            if meta_fd is not None:
                os.close(meta_fd)
            for leftover in (tmp_blob, tmp_meta):
                if leftover.exists() or leftover.is_symlink():
                    try:
                        leftover.unlink()
                    except OSError:
                        pass

    def _open_verified_fd(self, blob_path: Path, max_bytes: int) -> tuple[int, int]:
        if blob_path.is_symlink():
            raise StorageSecurityError("Symlinks cannot be opened as artifacts")
        if not blob_path.exists():
            raise ArtifactMissingError()
        try:
            fd = os.open(blob_path, os.O_RDONLY | O_NOFOLLOW | O_BINARY)
        except FileNotFoundError:
            raise ArtifactMissingError() from None
        except OSError as exc:
            raise StorageSecurityError("Unable to safely open artifact file") from exc

        try:
            st = os.fstat(fd)
            if st.st_size > max_bytes:
                raise ArtifactOversizedError(
                    f"Stored artifact size {st.st_size} exceeds read limit {max_bytes}"
                )
            return fd, st.st_size
        except Exception:
            os.close(fd)
            raise

    def stream(
        self,
        context: TenantContext,
        artifact: ArtifactVersionRef,
        *,
        chunk_size: int = DEFAULT_CHUNK_BYTES,
        max_bytes: int | None = None,
    ) -> Iterator[bytes]:
        if chunk_size <= 0:
            raise StorageConfigurationError("chunk_size must be positive")
        self._require_tenant_match(context, artifact)
        limit = self._effective_max_bytes(max_bytes)
        blob_path, _ = self._existing_blob_path(artifact)
        fd, _ = self._open_verified_fd(blob_path, limit)

        def _generator() -> Iterator[bytes]:
            digest = hashlib.sha256()
            read_total = 0
            try:
                while True:
                    chunk = os.read(fd, chunk_size)
                    if not chunk:
                        break
                    read_total += len(chunk)
                    if read_total > limit:
                        raise ArtifactOversizedError("Artifact exceeded bounded read size")
                    digest.update(chunk)
                    yield chunk
                if digest.hexdigest() != artifact.sha256:
                    raise ArtifactCorruptedError()
            finally:
                os.close(fd)

        return _generator()

    def read(self, context: TenantContext, artifact: ArtifactVersionRef) -> bytes:
        chunks = list(self.stream(context, artifact))
        return b"".join(chunks)

    def inspect(
        self, context: TenantContext, artifact: ArtifactVersionRef
    ) -> ArtifactMetadata:
        self._require_tenant_match(context, artifact)
        blob_path, meta_path = self._existing_blob_path(artifact)
        _reject_symlink(blob_path)
        if not blob_path.exists():
            raise ArtifactMissingError()
        _reject_symlink(meta_path)
        if not meta_path.exists():
            st = blob_path.stat()
            return ArtifactMetadata(
                ref=artifact,
                content_type="application/octet-stream",
                byte_size=st.st_size,
                created_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
            )
        payload = json.loads(meta_path.read_text(encoding="utf-8"))
        if payload.get("sha256") != artifact.sha256:
            raise ArtifactCorruptedError("Metadata digest mismatch")
        return ArtifactMetadata(
            ref=artifact,
            content_type=payload["content_type"],
            byte_size=payload["byte_size"],
            created_at=datetime.fromisoformat(payload["created_at"]),
        )

    def exists(self, context: TenantContext, artifact: ArtifactVersionRef) -> bool:
        self._require_tenant_match(context, artifact)
        blob_path, _ = self._existing_blob_path(artifact)
        _reject_symlink(blob_path)
        return blob_path.is_file()

    def delete_uncommitted(
        self, context: TenantContext, artifact: ArtifactVersionRef
    ) -> bool:
        """Best-effort compensating removal ONLY for a newly written uncommitted version directory."""
        self._require_tenant_match(context, artifact)
        blob_path, meta_path = self._existing_blob_path(artifact)
        version_dir = blob_path.parent
        _reject_symlink(version_dir)
        if not version_dir.exists():
            return False
        _reject_symlink(blob_path)
        _reject_symlink(meta_path)
        if meta_path.exists():
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            if payload.get("sha256") != artifact.sha256:
                raise ArtifactCorruptedError(
                    "Refusing compensating delete for mismatched artifact digest"
                )
        removed = False
        for entry in version_dir.iterdir():
            _reject_symlink(entry)
            if entry.is_file():
                entry.unlink()
                removed = True
        try:
            version_dir.rmdir()
        except OSError:
            pass
        return removed
