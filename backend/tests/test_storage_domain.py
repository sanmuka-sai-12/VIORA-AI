import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from viora.contracts import ArtifactVersionRef, TenantContext
from viora.ports import Storage
from viora.storage.public import (
    ArtifactCorruptedError,
    ArtifactExistsError,
    ArtifactMissingError,
    ArtifactOversizedError,
    LocalFilesystemArtifactStore,
    StorageAuthorizationError,
    StorageSecurityError,
    resolve_safe_subpath,
)


def _ctx() -> TenantContext:
    return TenantContext(
        actor_id=uuid4(),
        tenant_id=uuid4(),
        roles=frozenset({"institution_admin"}),
    )


def test_storage_immutable_roundtrip_and_ports_compatibility(tmp_path: Path) -> None:
    store = LocalFilesystemArtifactStore(tmp_path)
    ctx = _ctx()
    other_ctx = _ctx()
    payload = b"%PDF-1.4\nVIORA synthetic educational artifact bytes\n"
    doc_id = uuid4()

    stored = store.put_bytes(
        ctx,
        payload,
        kind="source_document",
        resource_id=doc_id,
        content_type="application/pdf",
    )
    ref = stored.metadata.ref
    assert stored.storage_key == f"tenants/{ctx.tenant_id}/{doc_id}/{ref.version_id}"
    assert stored.metadata.byte_size == len(payload)
    assert store.exists(ctx, ref) is True

    # Protocol compatibility with legacy ports.Storage
    legacy_port: Storage = store
    assert legacy_port.read(ctx, ref) == payload

    meta = store.inspect(ctx, ref)
    assert meta.content_type == "application/pdf"
    assert meta.ref == ref

    # Tenant key isolation enforced on every access method
    with pytest.raises(StorageAuthorizationError):
        store.read(other_ctx, ref)
    with pytest.raises(StorageAuthorizationError):
        list(store.stream(other_ctx, ref))
    with pytest.raises(StorageAuthorizationError):
        store.exists(other_ctx, ref)
    with pytest.raises(StorageAuthorizationError):
        store.inspect(other_ctx, ref)


def test_storage_streaming_oversize_and_incomplete_write_cleanup(
    tmp_path: Path,
) -> None:
    store = LocalFilesystemArtifactStore(tmp_path, max_artifact_bytes=1024)
    ctx = _ctx()
    res_id = uuid4()
    ver_id = uuid4()

    def failing_stream():
        yield b"chunk-one-"
        yield b"x" * 2048  # Exceeds max_artifact_bytes

    with pytest.raises(ArtifactOversizedError):
        store.put_stream(
            ctx,
            failing_stream(),
            kind="page_image",
            resource_id=res_id,
            content_type="image/png",
            version_id=ver_id,
        )

    # Incomplete temp files and target blob must not remain on disk
    tenant_dir = tmp_path / "tenants" / str(ctx.tenant_id) / str(res_id)
    leftover = list(tenant_dir.iterdir()) if tenant_dir.exists() else []
    assert leftover == []

    # Valid streamed write followed by chunked streaming read
    stored = store.put_stream(
        ctx,
        [b"hello ", b"education ", b"stream"],
        kind="ocr_json",
        resource_id=res_id,
        content_type="application/json",
    )
    streamed_out = b"".join(store.stream(ctx, stored.metadata.ref, chunk_size=4))
    assert streamed_out == b"hello education stream"


def test_storage_concurrent_immutable_writes(tmp_path: Path) -> None:
    store = LocalFilesystemArtifactStore(tmp_path)
    ctx = _ctx()
    artifact_id = uuid4()
    version_id = uuid4()

    def attempt_write(idx: int) -> str:
        try:
            store.put_bytes(
                ctx,
                f"payload-{idx}".encode(),
                kind="lesson_json",
                resource_id=artifact_id,
                artifact_id=artifact_id,
                version_id=version_id,
                content_type="application/json",
            )
            return "created"
        except ArtifactExistsError:
            return "exists"

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(attempt_write, range(6)))

    assert outcomes.count("created") == 1
    assert outcomes.count("exists") == 5


def test_storage_integrity_tamper_and_missing_errors(tmp_path: Path) -> None:
    store = LocalFilesystemArtifactStore(tmp_path)
    ctx = _ctx()
    res_id = uuid4()
    stored = store.put_bytes(
        ctx, b"clean binary data", kind="source_document", resource_id=res_id
    )
    ref = stored.metadata.ref

    # Missing artifact raises explicit ArtifactMissingError
    missing_ref = ArtifactVersionRef(
        tenant_id=ctx.tenant_id,
        kind="source_document",
        resource_id=uuid4(),
        version_id=uuid4(),
        sha256="0" * 64,
    )
    with pytest.raises(ArtifactMissingError):
        store.read(ctx, missing_ref)

    # Corrupted file bytes on disk fail SHA-256 verification on read & stream
    blob_path = (
        tmp_path
        / "tenants"
        / str(ctx.tenant_id)
        / str(ref.resource_id)
        / f"{ref.version_id}.blob"
    )
    blob_path.write_bytes(b"tampered bytes!!")

    with pytest.raises(ArtifactCorruptedError):
        store.read(ctx, ref)
    with pytest.raises(ArtifactCorruptedError):
        list(store.stream(ctx, ref))


def test_storage_path_traversal_and_symlink_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "vault"
    root.mkdir()
    store = LocalFilesystemArtifactStore(root)
    ctx = _ctx()

    for bad_segment in ("..", ".", "a/b", "a\\b", "C:evil", "bad\x00name", ""):
        with pytest.raises(StorageSecurityError):
            resolve_safe_subpath(root, "tenants", bad_segment)

    # Try creating a real OS symlink when permitted; fall back to verifying is_symlink check
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    link_target = root / "tenants"
    link_target.mkdir(parents=True, exist_ok=True)
    res_id = uuid4()
    tenant_dir = link_target / str(ctx.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    evil_dir = tenant_dir / str(res_id)

    try:
        os.symlink(tmp_path, evil_dir, target_is_directory=True)
        symlink_created = True
    except (OSError, NotImplementedError):
        symlink_created = False

    if symlink_created:
        with pytest.raises(StorageSecurityError):
            store.put_bytes(
                ctx,
                b"payload",
                kind="source_document",
                resource_id=res_id,
            )
    else:
        monkeypatch.setattr(Path, "is_symlink", lambda self: self.name == str(res_id))
        with pytest.raises(StorageSecurityError):
            resolve_safe_subpath(root, "tenants", str(ctx.tenant_id), str(res_id))

    # Junction / reparse point detection via _IS_JUNCTION guard also raises StorageSecurityError
    import viora.storage.local_adapter as la

    monkeypatch.setattr(la, "_IS_JUNCTION", lambda p: Path(p).name == "junc_dir")
    with pytest.raises(StorageSecurityError):
        resolve_safe_subpath(root, "tenants", "junc_dir")
