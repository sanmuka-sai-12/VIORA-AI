"""Version-aware, authorization-scoped extraction cache helpers and orphan reconciliation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .document_contracts import EvidenceReference, ExtractedDocument
from .models import ExtractionCacheEntry, PageOcrCacheEntry, Source
from .ocr import OcrLine, OcrPageResult

EXTRACTION_ALGORITHM_VERSION = "adaptive:2.0;spatial:2.0;norm:1.1"
PREPROCESSING_VERSION = "pil_ocr:2.0"


@dataclass(frozen=True)
class ExtractionCacheConfig:
    ocr_enabled: bool
    ocr_engine_id: str
    ocr_engine_version: str
    ocr_dpi: int
    enable_layout_fallback: bool
    enable_table_extraction: bool
    max_fallback_pages: int
    max_ocr_pages: int
    max_text_chars: int
    min_text_chars: int
    algorithm_version: str = EXTRACTION_ALGORITHM_VERSION
    preprocessing_version: str = PREPROCESSING_VERSION

    def config_digest(self) -> str:
        payload = json.dumps(
            {
                "ocr_enabled": self.ocr_enabled,
                "ocr_engine_id": self.ocr_engine_id,
                "ocr_engine_version": self.ocr_engine_version,
                "ocr_dpi": self.ocr_dpi,
                "enable_layout_fallback": self.enable_layout_fallback,
                "enable_table_extraction": self.enable_table_extraction,
                "max_fallback_pages": self.max_fallback_pages,
                "max_ocr_pages": self.max_ocr_pages,
                "max_text_chars": self.max_text_chars,
                "min_text_chars": self.min_text_chars,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


def build_scope_key(
    *,
    offering_id: UUID | None,
    origin_kind: str,
    owner_user_id: UUID | None,
) -> str:
    """Isolate cache scope across offerings and private student submissions."""
    if origin_kind == "teacher_resource" and offering_id is not None:
        return f"offering:{offering_id}:teacher_resource"
    if origin_kind == "student_submission" and offering_id is not None and owner_user_id is not None:
        return f"offering:{offering_id}:student:{owner_user_id}"
    return f"unscoped:{owner_user_id or 'none'}"


def build_extraction_cache_key(
    *,
    scope_key: str,
    content_sha256: str,
    input_format: str,
    config: ExtractionCacheConfig,
) -> str:
    payload = json.dumps(
        {
            "scope_key": scope_key,
            "content_sha256": content_sha256,
            "input_format": input_format,
            "algorithm_version": config.algorithm_version,
            "config_digest": config.config_digest(),
            "preprocessing_version": config.preprocessing_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def build_page_ocr_cache_key(
    *,
    scope_key: str,
    page_image_sha256: str,
    ocr_engine_id: str,
    ocr_engine_version: str,
    ocr_dpi: int,
    preprocessing_version: str = PREPROCESSING_VERSION,
) -> str:
    payload = json.dumps(
        {
            "scope_key": scope_key,
            "page_image_sha256": page_image_sha256,
            "ocr_engine_id": ocr_engine_id,
            "ocr_engine_version": ocr_engine_version,
            "ocr_dpi": ocr_dpi,
            "preprocessing_version": preprocessing_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def rebind_cached_document(
    cached: ExtractedDocument,
    *,
    tenant_id: UUID,
    source_id: UUID,
    version_id: UUID,
    source_version: int,
    source_sha256: str,
    title: str,
    created_at: datetime,
) -> ExtractedDocument:
    """Rebind cached extraction output and all EvidenceReferences to the exact target version."""
    rebound_evidence = tuple(
        EvidenceReference(
            evidence_id=ev.evidence_id,
            source_id=source_id,
            version_id=version_id,
            source_sha256=source_sha256,
            section_id=ev.section_id,
            block_id=ev.block_id,
            page_number=ev.page_number,
            bbox=ev.bbox,
            span=ev.span,
            excerpt=ev.excerpt,
        )
        for ev in cached.evidence_references
    )
    return cached.model_copy(
        update={
            "tenant_id": tenant_id,
            "source_id": source_id,
            "version_id": version_id,
            "source_version": source_version,
            "source_sha256": source_sha256,
            "title": title,
            "evidence_references": rebound_evidence,
            "created_at": created_at,
        }
    )


class SqlExtractionCache:
    """Tenant-bound extraction cache repository operating strictly inside RLS transactions."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def lookup(
        self,
        *,
        tenant_id: UUID,
        cache_key: str,
        scope_key: str,
        now: datetime,
    ) -> ExtractedDocument | None:
        row = self.session.scalar(
            select(ExtractionCacheEntry)
            .join(
                Source,
                (Source.tenant_id == ExtractionCacheEntry.tenant_id)
                & (Source.source_id == ExtractionCacheEntry.source_id),
            )
            .where(
                ExtractionCacheEntry.tenant_id == tenant_id,
                ExtractionCacheEntry.cache_key == cache_key,
                ExtractionCacheEntry.scope_key == scope_key,
                ExtractionCacheEntry.invalidated_at.is_(None),
                ExtractionCacheEntry.expires_at > now,
                Source.lifecycle_state == "active",
            )
        )
        if row is None:
            return None
        self.session.execute(
            update(ExtractionCacheEntry)
            .where(
                ExtractionCacheEntry.tenant_id == tenant_id,
                ExtractionCacheEntry.cache_key == cache_key,
            )
            .values(
                hit_count=ExtractionCacheEntry.hit_count + 1,
                last_hit_at=now,
            )
        )
        return ExtractedDocument.model_validate(row.document)

    def put(
        self,
        *,
        tenant_id: UUID,
        cache_key: str,
        scope_key: str,
        content_sha256: str,
        config: ExtractionCacheConfig,
        input_format: str,
        source_id: UUID,
        version_id: UUID,
        derived_extraction_id: UUID,
        document: ExtractedDocument,
        ttl_seconds: int,
        now: datetime,
    ) -> None:
        expires_at = now + timedelta(seconds=ttl_seconds)
        stmt = (
            pg_insert(ExtractionCacheEntry)
            .values(
                tenant_id=tenant_id,
                cache_key=cache_key,
                scope_key=scope_key,
                content_sha256=content_sha256,
                algorithm_version=config.algorithm_version,
                config_digest=config.config_digest(),
                preprocessing_version=config.preprocessing_version,
                input_format=input_format,
                source_id=source_id,
                version_id=version_id,
                derived_extraction_id=derived_extraction_id,
                document=document.model_dump(mode="json"),
                hit_count=0,
                last_hit_at=None,
                expires_at=expires_at,
                invalidated_at=None,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "cache_key"],
                set_={
                    "derived_extraction_id": derived_extraction_id,
                    "source_id": source_id,
                    "version_id": version_id,
                    "document": document.model_dump(mode="json"),
                    "expires_at": expires_at,
                    "invalidated_at": None,
                },
            )
        )
        self.session.execute(stmt)

    def invalidate_source(
        self, *, tenant_id: UUID, source_id: UUID, now: datetime
    ) -> int:
        res = self.session.execute(
            update(ExtractionCacheEntry)
            .where(
                ExtractionCacheEntry.tenant_id == tenant_id,
                ExtractionCacheEntry.source_id == source_id,
                ExtractionCacheEntry.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )
        return int(res.rowcount or 0)

    def invalidate_key(
        self, *, tenant_id: UUID, cache_key: str, now: datetime
    ) -> int:
        res = self.session.execute(
            update(ExtractionCacheEntry)
            .where(
                ExtractionCacheEntry.tenant_id == tenant_id,
                ExtractionCacheEntry.cache_key == cache_key,
                ExtractionCacheEntry.invalidated_at.is_(None),
            )
            .values(invalidated_at=now)
        )
        return int(res.rowcount or 0)

    def lookup_page_ocr(
        self,
        *,
        tenant_id: UUID,
        page_cache_key: str,
        scope_key: str,
        now: datetime,
    ) -> OcrPageResult | None:
        row = self.session.scalar(
            select(PageOcrCacheEntry).where(
                PageOcrCacheEntry.tenant_id == tenant_id,
                PageOcrCacheEntry.page_cache_key == page_cache_key,
                PageOcrCacheEntry.scope_key == scope_key,
                PageOcrCacheEntry.invalidated_at.is_(None),
                PageOcrCacheEntry.expires_at > now,
            )
        )
        if row is None:
            return None
        payload = dict(row.ocr_result)
        lines = tuple(OcrLine(**item) for item in payload.get("lines", ()))
        return OcrPageResult(
            lines=lines,
            image_width=int(payload["image_width"]),
            image_height=int(payload["image_height"]),
            engine_id=str(payload["engine_id"]),
            engine_version=str(payload["engine_version"]),
            mean_confidence=float(payload["mean_confidence"]),
            preprocess_applied=bool(payload.get("preprocess_applied", False)),
        )

    def put_page_ocr(
        self,
        *,
        tenant_id: UUID,
        page_cache_key: str,
        scope_key: str,
        page_image_sha256: str,
        result: OcrPageResult,
        ttl_seconds: int,
        now: datetime,
    ) -> None:
        expires_at = now + timedelta(seconds=ttl_seconds)
        serialized = {
            "lines": [
                {
                    "text": ln.text,
                    "confidence": ln.confidence,
                    "x0": ln.x0,
                    "y0": ln.y0,
                    "x1": ln.x1,
                    "y1": ln.y1,
                }
                for ln in result.lines
            ],
            "image_width": result.image_width,
            "image_height": result.image_height,
            "engine_id": result.engine_id,
            "engine_version": result.engine_version,
            "mean_confidence": result.mean_confidence,
            "preprocess_applied": result.preprocess_applied,
        }
        stmt = (
            pg_insert(PageOcrCacheEntry)
            .values(
                tenant_id=tenant_id,
                page_cache_key=page_cache_key,
                scope_key=scope_key,
                page_image_sha256=page_image_sha256,
                ocr_engine_id=result.engine_id,
                ocr_engine_version=result.engine_version,
                ocr_result=serialized,
                expires_at=expires_at,
                invalidated_at=None,
                created_at=now,
            )
            .on_conflict_do_update(
                index_elements=["tenant_id", "page_cache_key"],
                set_={
                    "ocr_result": serialized,
                    "expires_at": expires_at,
                    "invalidated_at": None,
                },
            )
        )
        self.session.execute(stmt)

    def purge_expired_and_invalidated(
        self, *, tenant_id: UUID, now: datetime
    ) -> dict[str, int]:
        """Remove expired or invalidated cache rows inside the tenant RLS boundary."""
        del_doc = self.session.execute(
            delete(ExtractionCacheEntry).where(
                ExtractionCacheEntry.tenant_id == tenant_id,
                (
                    (ExtractionCacheEntry.invalidated_at.is_not(None))
                    | (ExtractionCacheEntry.expires_at <= now)
                ),
            )
        )
        del_page = self.session.execute(
            delete(PageOcrCacheEntry).where(
                PageOcrCacheEntry.tenant_id == tenant_id,
                (
                    (PageOcrCacheEntry.invalidated_at.is_not(None))
                    | (PageOcrCacheEntry.expires_at <= now)
                ),
            )
        )
        return {
            "doc_cache_purged": int(del_doc.rowcount or 0),
            "page_cache_purged": int(del_page.rowcount or 0),
        }


def cleanup_stale_storage_temps(
    storage_root: Path,
    *,
    min_age_seconds: int = 300,
) -> int:
    """Best-effort cleanup of abandoned '.tmp-*' files left by crashed writers (never touches committed '.blob')."""
    if not storage_root.exists():
        return 0
    cutoff = datetime.now(UTC).timestamp() - max(0, min_age_seconds)
    removed = 0
    for path in storage_root.rglob(".tmp-*"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            if path.stat().st_mtime <= cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def reconcile_orphan_artifacts(
    storage_root: Path,
    *,
    list_referenced_storage_keys: Any,
    min_age_seconds: int = 3600,
) -> dict[str, int]:
    """Reconcile unreferenced committed '.blob'/'.meta.json' files and stale '.tmp-*' artifacts."""
    temps_removed = cleanup_stale_storage_temps(
        storage_root, min_age_seconds=min(max(0, min_age_seconds), 300)
    )
    tenants_dir = storage_root / "tenants"
    if not tenants_dir.exists() or tenants_dir.is_symlink():
        return {"temps_removed": temps_removed, "blobs_removed": 0, "metas_removed": 0}

    cutoff = datetime.now(UTC).timestamp() - max(0, min_age_seconds)
    blobs_removed = 0
    metas_removed = 0
    for tenant_dir in tenants_dir.iterdir():
        if tenant_dir.is_symlink() or not tenant_dir.is_dir():
            continue
        try:
            tenant_id = UUID(tenant_dir.name)
        except ValueError:
            continue
        referenced_keys: set[tuple[UUID, UUID]] = set(
            list_referenced_storage_keys(tenant_id)
        )
        for resource_dir in tenant_dir.iterdir():
            if resource_dir.is_symlink() or not resource_dir.is_dir():
                continue
            try:
                resource_id = UUID(resource_dir.name)
            except ValueError:
                continue
            for item in list(resource_dir.iterdir()):
                if item.is_symlink() or not item.is_file():
                    continue
                if not item.name.endswith(".blob"):
                    continue
                stem = item.name[:-5]
                try:
                    version_id = UUID(stem)
                except ValueError:
                    continue
                if (resource_id, version_id) in referenced_keys:
                    continue
                try:
                    if item.stat().st_mtime > cutoff:
                        continue
                    item.unlink()
                    blobs_removed += 1
                    meta_path = resource_dir / f"{stem}.meta.json"
                    if meta_path.is_file() and not meta_path.is_symlink():
                        meta_path.unlink()
                        metas_removed += 1
                except OSError:
                    continue
    return {
        "temps_removed": temps_removed,
        "blobs_removed": blobs_removed,
        "metas_removed": metas_removed,
    }
