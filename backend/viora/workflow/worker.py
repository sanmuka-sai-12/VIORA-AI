"""Independent durable background worker for parallel extraction and OCR."""

from __future__ import annotations

import argparse
import contextlib
import logging
import os
import signal
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ..ai.public import (
    AIError,
    AIGateway,
    AITransientError,
    build_ai_gateway_from_settings,
)
from ..authorization.public import (
    AuthorizationService,
    bind_tenant,
    require_application_role,
)
from ..authorization.public import register_models as register_authorization
from ..config import Settings
from ..contracts import Actor, ResourceRef, TenantContext
from ..db import Database
from ..identity.public import register_models as register_identity
from ..institution.public import register_models as register_institution
from ..knowledge.public import (
    ConceptExtractionWorkerHandler,
    KnowledgeIndexingWorkerHandler,
    TextEmbedder,
    build_embedder_from_settings,
)
from ..knowledge.public import register_models as register_knowledge
from ..security import AccessError
from ..sources.public import (
    RAPIDOCR_EXTRACTOR_ID,
    RAPIDOCR_EXTRACTOR_VERSION,
    AdaptiveDocumentExtractor,
    ExtractionCacheConfig,
    ExtractionCancelledError,
    OcrBackend,
    OcrPageResult,
    RapidOcrBackend,
    SourceIngestionLimits,
    SourceProcessingStatus,
    SqlExtractionCache,
    SqlSourceRepository,
    build_extraction_cache_key,
    build_page_ocr_cache_key,
    build_scope_key,
    clamp_source_limits,
    cleanup_stale_storage_temps,
    rebind_cached_document,
    reconcile_orphan_artifacts,
)
from ..sources.public import register_models as register_sources
from ..storage.public import ArtifactStore, StorageError, local_artifact_store
from .ports import JobCancelledError, StaleWorkerFenceError, WorkerLeaseController
from .repository import SqlWorkflowWorkerStore
from .schemas import ClaimedWorkflowJob, LeaseRenewalResult

logger = logging.getLogger("viora.workflow.worker")


class _DbLeaseController(WorkerLeaseController):
    """Thread-safe lease heartbeat and cooperative cancellation controller for one claimed job."""

    def __init__(
        self,
        *,
        db: Database,
        job: ClaimedWorkflowJob,
        lease_seconds: int,
    ) -> None:
        self._db = db
        self._job = job
        self._lease_seconds = lease_seconds
        self._lock = threading.Lock()
        self.lease_lost = False
        self.cancel_requested = False

    def renew(
        self,
        *,
        stage: str,
        progress_metadata: dict[str, str | int | float | bool],
        checkpoint_data: dict[str, object] | None = None,
    ) -> LeaseRenewalResult:
        with self._lock:
            with self._db.sessions.begin() as session:
                require_application_role(session)
                bind_tenant(session, self._job.tenant_id)
                res = SqlWorkflowWorkerStore.renew_lease(
                    session,
                    tenant_id=self._job.tenant_id,
                    job_id=self._job.job_id,
                    worker_id=self._job.lease_owner,
                    fencing_token=self._job.fencing_token,
                    lease_seconds=self._lease_seconds,
                    stage=stage,
                    progress_metadata=progress_metadata,
                    checkpoint_data=checkpoint_data,
                    now=datetime.now(UTC),
                )
            if not res.renewed:
                self.lease_lost = True
            if res.cancel_requested:
                self.cancel_requested = True
            return res

    def check_cancelled(self) -> bool:
        return self.lease_lost or self.cancel_requested


def _processing_status_for_doc(doc: Any) -> SourceProcessingStatus:
    if doc.quality.overall_status == "ocr_required":
        return "ocr_required"
    if doc.quality.overall_status == "unreadable":
        return "unreadable"
    if doc.quality.warning_count > 0:
        return "partial_extraction"
    return "extracted"


def _doc_result_metadata(doc: Any, *, cache_hit: bool) -> dict[str, str | int | float | bool]:
    return {
        "overall_quality": doc.quality.overall_status,
        "usable_for_digital_ingestion": doc.quality.usable_for_digital_ingestion,
        "total_pages": doc.quality.total_pages,
        "digital_text_pages": doc.quality.digital_text_pages,
        "pages_needing_layout": doc.quality.pages_needing_layout,
        "pages_needing_table": doc.quality.pages_needing_table,
        "pages_needing_ocr": doc.quality.pages_needing_ocr,
        "pages_with_visuals": doc.quality.pages_with_visuals,
        "char_count": len(doc.normalized_text),
        "block_count": len(doc.text_blocks),
        "table_count": len(doc.tables),
        "figure_count": len(doc.figures),
        "formula_count": len(doc.formulas),
        "warning_count": doc.quality.warning_count,
        "cache_hit": cache_hit,
    }


class ExtractionWorkerHandler:
    """Executes source extraction jobs with reauthorization, caching, and fenced commits."""

    def __init__(
        self,
        *,
        db: Database,
        access: AuthorizationService,
        artifact_store: ArtifactStore,
        limits: SourceIngestionLimits,
        extractor: AdaptiveDocumentExtractor,
        cache_ttl_seconds: int = 7 * 24 * 3600,
        heartbeat_seconds: int = 5,
    ) -> None:
        self.db = db
        self.access = access
        self.artifact_store = artifact_store
        self.limits = limits
        self.extractor = extractor
        self.cache_ttl_seconds = cache_ttl_seconds
        self.heartbeat_seconds = max(1, heartbeat_seconds)

        ocr_ready = (
            extractor.ocr_backend is not None and extractor.ocr_backend.is_available()
        )
        ocr_id = (
            extractor.ocr_backend.engine_id
            if (ocr_ready and extractor.ocr_backend is not None)
            else RAPIDOCR_EXTRACTOR_ID
        )
        ocr_ver = (
            extractor.ocr_backend.engine_version
            if (ocr_ready and extractor.ocr_backend is not None)
            else RAPIDOCR_EXTRACTOR_VERSION
        )
        self.cache_config = ExtractionCacheConfig(
            ocr_enabled=ocr_ready,
            ocr_engine_id=ocr_id if ocr_ready else "disabled",
            ocr_engine_version=ocr_ver if ocr_ready else "0",
            ocr_dpi=extractor.ocr_dpi,
            enable_layout_fallback=extractor.enable_layout_fallback,
            enable_table_extraction=extractor.enable_table_extraction,
            max_fallback_pages=extractor.max_fallback_pages,
            max_ocr_pages=extractor.max_ocr_pages,
            max_text_chars=limits.max_text_chars,
            min_text_chars=limits.min_text_chars,
        )

    def execute(
        self,
        job: ClaimedWorkflowJob,
        *,
        heartbeat: WorkerLeaseController,
    ) -> tuple[UUID, dict[str, str | int | float | bool]]:
        actor = Actor(actor_id=job.submitted_by_user_id)
        source_ref = ResourceRef(
            tenant_id=job.tenant_id, kind="source", resource_id=job.source_id
        )

        # Stage 1: Reauthorize + read metadata + check scoped cache (short TX)
        heartbeat.renew(stage="reauthorizing", progress_metadata={"stage": "reauthorizing"})
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before reauthorization")

        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            repo = SqlSourceRepository(scope.session)
            src_info = repo.get_source_access(job.tenant_id, job.source_id)
            ver_meta = repo.get_version_meta(job.tenant_id, job.source_id, job.version_id)
            if src_info is None or ver_meta is None:
                raise AccessError(404, "source_missing", "Source or version unavailable")
            ver_num, ver_sha, ver_fmt, ver_storage_ref = ver_meta
            if ver_storage_ref != job.input_artifact:
                raise AccessError(
                    409,
                    "artifact_mismatch",
                    "Job artifact reference does not match immutable source version",
                )
            scope_key = build_scope_key(
                offering_id=src_info.offering_id,
                origin_kind=src_info.origin_kind,
                owner_user_id=src_info.owner_user_id,
            )
            cache_key = build_extraction_cache_key(
                scope_key=scope_key,
                content_sha256=ver_sha,
                input_format=ver_fmt,
                config=self.cache_config,
            )
            cached_doc = SqlExtractionCache(scope.session).lookup(
                tenant_id=job.tenant_id,
                cache_key=cache_key,
                scope_key=scope_key,
                now=datetime.now(UTC),
            )
            worker_tenant_ctx: TenantContext = scope.context

        # Stage 2: Extract outside DB transactions if cache miss
        t0 = time.monotonic_ns()
        if cached_doc is not None:
            cache_hit = True
            extracted_doc = rebind_cached_document(
                cached_doc,
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                version_id=job.version_id,
                source_version=ver_num,
                source_sha256=ver_sha,
                title=src_info.title,
                created_at=datetime.now(UTC),
            )
            heartbeat.renew(
                stage="cache_hit",
                progress_metadata={"stage": "cache_hit", "cache_hit": True},
            )
        else:
            cache_hit = False
            heartbeat.renew(
                stage="loading_artifact",
                progress_metadata={"stage": "loading_artifact", "cache_hit": False},
            )
            if heartbeat.check_cancelled():
                raise JobCancelledError("Cancelled before loading artifact")
            raw_bytes = self.artifact_store.read(worker_tenant_ctx, job.input_artifact)

            stop_hb = threading.Event()
            stage_state: dict[str, Any] = {
                "stage": "extracting",
                "meta": {"stage": "extracting", "cache_hit": False},
            }

            def _hb_loop() -> None:
                while not stop_hb.wait(self.heartbeat_seconds):
                    try:
                        res = heartbeat.renew(
                            stage=str(stage_state["stage"]),
                            progress_metadata=dict(stage_state["meta"]),
                        )
                        if not res.renewed or res.cancel_requested:
                            break
                    except Exception:  # noqa: BLE001
                        break

            def _progress_cb(
                stage: str, meta: dict[str, str | int | float | bool]
            ) -> None:
                stage_state["stage"] = stage
                stage_state["meta"] = {**meta, "stage": stage, "cache_hit": False}
                heartbeat.renew(
                    stage=stage,
                    progress_metadata=dict(stage_state["meta"]),
                    checkpoint_data={"stage": stage, **meta},
                )

            def _page_cache_lookup(img_sha: str) -> OcrPageResult | None:
                if not self.cache_config.ocr_enabled:
                    return None
                p_key = build_page_ocr_cache_key(
                    scope_key=scope_key,
                    page_image_sha256=img_sha,
                    ocr_engine_id=self.cache_config.ocr_engine_id,
                    ocr_engine_version=self.cache_config.ocr_engine_version,
                    ocr_dpi=self.cache_config.ocr_dpi,
                )
                with self.db.sessions.begin() as s:
                    require_application_role(s)
                    bind_tenant(s, job.tenant_id)
                    return SqlExtractionCache(s).lookup_page_ocr(
                        tenant_id=job.tenant_id,
                        page_cache_key=p_key,
                        scope_key=scope_key,
                        now=datetime.now(UTC),
                    )

            def _page_cache_store(img_sha: str, ocr_res: OcrPageResult) -> None:
                if not self.cache_config.ocr_enabled:
                    return
                p_key = build_page_ocr_cache_key(
                    scope_key=scope_key,
                    page_image_sha256=img_sha,
                    ocr_engine_id=ocr_res.engine_id,
                    ocr_engine_version=ocr_res.engine_version,
                    ocr_dpi=self.cache_config.ocr_dpi,
                )
                with self.db.sessions.begin() as s:
                    require_application_role(s)
                    bind_tenant(s, job.tenant_id)
                    SqlExtractionCache(s).put_page_ocr(
                        tenant_id=job.tenant_id,
                        page_cache_key=p_key,
                        scope_key=scope_key,
                        page_image_sha256=img_sha,
                        result=ocr_res,
                        ttl_seconds=self.cache_ttl_seconds,
                        now=datetime.now(UTC),
                    )

            hb_thread = threading.Thread(target=_hb_loop, daemon=True)
            hb_thread.start()
            try:
                extracted_doc = self.extractor.extract(
                    tenant_id=job.tenant_id,
                    source_id=job.source_id,
                    version_id=job.version_id,
                    source_version=ver_num,
                    source_sha256=ver_sha,
                    input_format=ver_fmt,
                    title=src_info.title,
                    raw_bytes=raw_bytes,
                    limits=self.limits,
                    progress_callback=_progress_cb,
                    cancel_check=heartbeat.check_cancelled,
                    page_ocr_cache_lookup=_page_cache_lookup,
                    page_ocr_cache_store=_page_cache_store,
                )
            except ExtractionCancelledError as exc:
                raise JobCancelledError(str(exc)) from None
            finally:
                stop_hb.set()
                hb_thread.join(timeout=1.0)

        if len(extracted_doc.normalized_text) > self.limits.max_text_chars:
            raise AccessError(
                413,
                "text_limit_exceeded",
                f"Normalized text length {len(extracted_doc.normalized_text)} exceeds limit",
            )
        if getattr(heartbeat, "lease_lost", False):
            raise StaleWorkerFenceError("Lease lost during extraction")
        if heartbeat.check_cancelled():
            raise JobCancelledError("Job cancelled during extraction")

        extract_ms = round((time.monotonic_ns() - t0) / 1_000_000.0, 3)
        proc_status = _processing_status_for_doc(extracted_doc)
        result_meta = _doc_result_metadata(extracted_doc, cache_hit=cache_hit)

        # Stage 3: Reauthorize actor & source, persist derived_extractions + cache + fenced finalize in ONE TX
        now = datetime.now(UTC)
        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            repo = SqlSourceRepository(scope.session)
            derived = repo.append_derived_extraction(
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                version_id=job.version_id,
                source_sha256=ver_sha,
                document=extracted_doc,
                processing_status=proc_status,
                extraction_ms=extract_ms,
                processing_metadata=result_meta,
                now=now,
                job_id=job.job_id,
                cache_hit=cache_hit,
                cache_key=cache_key,
            )
            if not cache_hit and proc_status != "ocr_required":
                SqlExtractionCache(scope.session).put(
                    tenant_id=job.tenant_id,
                    cache_key=cache_key,
                    scope_key=scope_key,
                    content_sha256=ver_sha,
                    config=self.cache_config,
                    input_format=ver_fmt,
                    source_id=job.source_id,
                    version_id=job.version_id,
                    derived_extraction_id=derived.extraction_id,
                    document=extracted_doc,
                    ttl_seconds=self.cache_ttl_seconds,
                    now=now,
                )
            SqlWorkflowWorkerStore.finalize_succeeded(
                scope.session,
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=job.lease_owner,
                fencing_token=job.fencing_token,
                extraction_id=derived.extraction_id,
                result_metadata=result_meta,
                progress_metadata={**result_meta, "stage": "completed"},
                now=now,
            )
            scope.audit.change(
                scope.context,
                "workflow.job.succeeded",
                ResourceRef(
                    tenant_id=job.tenant_id,
                    kind="workflow_job",
                    resource_id=job.job_id,
                ),
            )
        return derived.extraction_id, result_meta


class WorkflowWorkerRunner:
    """Durable PostgreSQL polling worker with bounded concurrency and backpressure."""

    def __init__(
        self,
        *,
        db: Database,
        handler: ExtractionWorkerHandler,
        concept_handler: ConceptExtractionWorkerHandler | None = None,
        index_handler: KnowledgeIndexingWorkerHandler | None = None,
        worker_id: str | None = None,
        concurrency: int = 2,
        lease_seconds: int = 30,
        poll_interval_seconds: float = 0.25,
        retry_base_seconds: float = 1.0,
    ) -> None:
        self.db = db
        self.handler = handler
        self.concept_handler = concept_handler
        self.index_handler = index_handler
        self.worker_id = worker_id or f"viora-worker-{uuid4().hex[:12]}"
        self.concurrency = max(1, concurrency)
        self.lease_seconds = max(5, lease_seconds)
        self.poll_interval_seconds = max(0.05, poll_interval_seconds)
        self.retry_base_seconds = max(0.1, retry_base_seconds)

    def close(self) -> None:
        """Release worker page-pool processes and AI HTTP connections owned by runner."""
        close_fn = getattr(self.handler.extractor, "close", None)
        if callable(close_fn):
            close_fn()
        if self.concept_handler is not None and self.concept_handler.gateway is not None:
            gw_close = getattr(self.concept_handler.gateway, "close", None)
            if callable(gw_close):
                gw_close()

    def reconcile_orphans(self, *, min_age_seconds: int = 3600) -> dict[str, int]:
        """Reconcile stale temp files and unreferenced committed blob artifacts."""
        store_root = getattr(self.handler.artifact_store, "root", None)
        if store_root is None:
            return {"temps_removed": 0, "blobs_removed": 0, "metas_removed": 0}

        def _list_keys(tenant_id: UUID) -> set[tuple[UUID, UUID]]:
            with self.db.sessions.begin() as s:
                require_application_role(s)
                bind_tenant(s, tenant_id)
                return SqlSourceRepository(s).list_storage_keys(tenant_id)

        return reconcile_orphan_artifacts(
            store_root,
            list_referenced_storage_keys=_list_keys,
            min_age_seconds=min_age_seconds,
        )

    def _execute_claimed_job(self, job: ClaimedWorkflowJob) -> str:
        controller = _DbLeaseController(
            db=self.db, job=job, lease_seconds=self.lease_seconds
        )
        try:
            if job.operation_kind == "concept_extract":
                if self.concept_handler is None:
                    raise AccessError(
                        503,
                        "concept_handler_unavailable",
                        "No concept extraction handler registered in worker",
                    )
                self.concept_handler.execute(job, heartbeat=controller)
            elif job.operation_kind == "knowledge_index":
                if self.index_handler is None:
                    raise AccessError(
                        503,
                        "index_handler_unavailable",
                        "No knowledge indexing handler registered in worker",
                    )
                self.index_handler.execute(job, heartbeat=controller)
            else:
                self.handler.execute(job, heartbeat=controller)
            return "succeeded"
        except StaleWorkerFenceError:
            logger.warning(
                "Stale worker fence rejected output for job=%s tenant=%s token=%s",
                job.job_id,
                job.tenant_id,
                job.fencing_token,
            )
            return "fenced"
        except JobCancelledError:
            with contextlib.suppress(Exception), self.db.sessions.begin() as s:
                require_application_role(s)
                bind_tenant(s, job.tenant_id)
                SqlWorkflowWorkerStore.finalize_cancelled(
                    s,
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=job.lease_owner,
                    fencing_token=job.fencing_token,
                    now=datetime.now(UTC),
                )
            return "cancelled"
        except AIError as exc:
            retryable = isinstance(exc, AITransientError)
            backoff = self.retry_base_seconds * (2 ** max(0, job.attempt_count - 1))
            try:
                with self.db.sessions.begin() as s:
                    require_application_role(s)
                    bind_tenant(s, job.tenant_id)
                    return SqlWorkflowWorkerStore.finalize_failure_or_retry(
                        s,
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        worker_id=job.lease_owner,
                        fencing_token=job.fencing_token,
                        error_code=exc.code,
                        error_message=exc.message,
                        retryable=retryable,
                        backoff_seconds=backoff,
                        now=datetime.now(UTC),
                    )
            except StaleWorkerFenceError:
                return "fenced"
        except AccessError as exc:
            # Authorization revocation or permanent validation failure: do not retry.
            retryable = exc.status >= 500
            err_code = (
                "authorization_revoked"
                if exc.status in {401, 403}
                else exc.code
            )
            backoff = self.retry_base_seconds * (2 ** max(0, job.attempt_count - 1))
            try:
                with self.db.sessions.begin() as s:
                    require_application_role(s)
                    bind_tenant(s, job.tenant_id)
                    return SqlWorkflowWorkerStore.finalize_failure_or_retry(
                        s,
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        worker_id=job.lease_owner,
                        fencing_token=job.fencing_token,
                        error_code=err_code,
                        error_message=str(exc),
                        retryable=retryable,
                        backoff_seconds=backoff,
                        now=datetime.now(UTC),
                    )
            except StaleWorkerFenceError:
                return "fenced"
        except (StorageError, OSError, TimeoutError, RuntimeError) as exc:
            backoff = self.retry_base_seconds * (2 ** max(0, job.attempt_count - 1))
            try:
                with self.db.sessions.begin() as s:
                    require_application_role(s)
                    bind_tenant(s, job.tenant_id)
                    return SqlWorkflowWorkerStore.finalize_failure_or_retry(
                        s,
                        tenant_id=job.tenant_id,
                        job_id=job.job_id,
                        worker_id=job.lease_owner,
                        fencing_token=job.fencing_token,
                        error_code="worker_execution_error",
                        error_message=str(exc),
                        retryable=True,
                        backoff_seconds=backoff,
                        now=datetime.now(UTC),
                    )
            except StaleWorkerFenceError:
                return "fenced"

    def claim_and_process_one(self) -> str | None:
        with self.db.sessions.begin() as session:
            require_application_role(session)
            job = SqlWorkflowWorkerStore.claim_next_job(
                session,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                now=datetime.now(UTC),
            )
        if job is None:
            return None
        return self._execute_claimed_job(job)

    def drain(self, *, max_jobs: int | None = None) -> dict[str, int]:
        """Claim and process available jobs using bounded concurrency until queue is empty."""
        counts: dict[str, int] = {
            "claimed": 0,
            "succeeded": 0,
            "retry_wait": 0,
            "failed": 0,
            "cancelled": 0,
            "fenced": 0,
        }
        in_flight: set[Future[str]] = set()
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            while True:
                while len(in_flight) < self.concurrency and (
                    max_jobs is None or counts["claimed"] < max_jobs
                ):
                    with self.db.sessions.begin() as session:
                        require_application_role(session)
                        job = SqlWorkflowWorkerStore.claim_next_job(
                            session,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                            now=datetime.now(UTC),
                        )
                    if job is None:
                        break
                    counts["claimed"] += 1
                    in_flight.add(pool.submit(self._execute_claimed_job, job))

                if not in_flight:
                    break
                done, in_flight = wait(in_flight, return_when=FIRST_COMPLETED)
                for fut in done:
                    outcome = fut.result()
                    counts[outcome] = counts.get(outcome, 0) + 1
        return counts

    def run_forever(self, stop_event: threading.Event) -> None:
        """Continuously poll with backpressure until stop_event is set."""
        warm_fn = getattr(self.handler.extractor, "warm_pool", None)
        if callable(warm_fn):
            warm_fn()
        in_flight: set[Future[str]] = set()
        last_cleanup = time.monotonic()
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            while not stop_event.is_set() or in_flight:
                while not stop_event.is_set() and len(in_flight) < self.concurrency:
                    with self.db.sessions.begin() as session:
                        require_application_role(session)
                        job = SqlWorkflowWorkerStore.claim_next_job(
                            session,
                            worker_id=self.worker_id,
                            lease_seconds=self.lease_seconds,
                            now=datetime.now(UTC),
                        )
                    if job is None:
                        break
                    in_flight.add(pool.submit(self._execute_claimed_job, job))

                if in_flight:
                    done, in_flight = wait(
                        in_flight,
                        timeout=self.poll_interval_seconds,
                        return_when=FIRST_COMPLETED,
                    )
                    for fut in done:
                        with contextlib.suppress(Exception):
                            fut.result()
                else:
                    stop_event.wait(self.poll_interval_seconds)

                if (time.monotonic() - last_cleanup) >= 60.0:
                    last_cleanup = time.monotonic()
                    store_root = getattr(self.handler.artifact_store, "root", None)
                    if store_root is not None:
                        with contextlib.suppress(OSError):
                            cleanup_stale_storage_temps(store_root)
                        with contextlib.suppress(Exception):
                            self.reconcile_orphans(min_age_seconds=3600)


def build_worker_runner(
    settings: Settings,
    *,
    db: Database | None = None,
    artifact_store: ArtifactStore | None = None,
    ocr_backend: OcrBackend | None = None,
    ai_gateway: AIGateway | None = None,
    embedder: TextEmbedder | None = None,
    worker_id: str | None = None,
) -> WorkflowWorkerRunner:
    """Construct a fully configured worker runner from application Settings."""
    register_authorization()
    register_institution()
    register_identity()
    register_sources()
    register_knowledge()
    from .public import register_models as register_workflow

    register_workflow()

    database = db or Database(settings)
    access = AuthorizationService(database, None)
    access.validate_database()

    limits = clamp_source_limits(
        max_bytes=settings.source_max_bytes,
        max_pdf_pages=settings.source_max_pdf_pages,
        max_text_chars=settings.source_max_text_chars,
        min_text_chars=settings.source_min_text_chars,
    )
    store = artifact_store or local_artifact_store(
        settings.media_storage_dir, max_artifact_bytes=limits.max_bytes
    )
    cpu_budget = max(1, os.cpu_count() or 2)
    effective_concurrency = max(1, min(settings.worker_concurrency, cpu_budget))
    effective_page_workers = max(
        1, min(settings.worker_page_workers, max(1, cpu_budget // effective_concurrency))
    )
    intra_op_thread_budget = max(
        1, cpu_budget // max(1, effective_concurrency * effective_page_workers)
    )
    effective_ocr: OcrBackend | None = ocr_backend
    if effective_ocr is None and settings.worker_enable_ocr:
        candidate = RapidOcrBackend(intra_op_threads=intra_op_thread_budget)
        if candidate.is_available():
            candidate.warm_up()
            effective_ocr = candidate
    extractor = AdaptiveDocumentExtractor(
        ocr_backend=effective_ocr,
        page_workers=effective_page_workers,
        task_timeout_seconds=settings.worker_task_timeout_seconds,
        isolate_untrusted_parse=True,
        intra_op_thread_budget=intra_op_thread_budget,
    )
    handler = ExtractionWorkerHandler(
        db=database,
        access=access,
        artifact_store=store,
        limits=limits,
        extractor=extractor,
        cache_ttl_seconds=settings.extraction_cache_ttl_seconds,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
    )
    effective_gateway = (
        ai_gateway
        if ai_gateway is not None
        else build_ai_gateway_from_settings(settings)
    )
    concept_handler = ConceptExtractionWorkerHandler(
        db=database,
        access=access,
        gateway=effective_gateway,
        cache_ttl_seconds=settings.extraction_cache_ttl_seconds,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
    )
    effective_embedder = (
        embedder if embedder is not None else build_embedder_from_settings(settings)
    )
    index_handler = KnowledgeIndexingWorkerHandler(
        db=database,
        access=access,
        embedder=effective_embedder,
        heartbeat_seconds=settings.worker_heartbeat_seconds,
    )
    return WorkflowWorkerRunner(
        db=database,
        handler=handler,
        concept_handler=concept_handler,
        index_handler=index_handler,
        worker_id=worker_id,
        concurrency=effective_concurrency,
        lease_seconds=settings.worker_lease_seconds,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VIORA-AI durable extraction worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Drain currently queued/due jobs and exit.",
    )
    parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="Maximum jobs to process in --once mode.",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default=None,
        help="Explicit worker instance identifier.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.load()
    runner = build_worker_runner(settings, worker_id=args.worker_id)
    try:
        if args.once:
            summary = runner.drain(max_jobs=args.max_jobs)
            logger.info("Worker --once completed: %s", summary)
            return 0

        stop_event = threading.Event()

        def _handle_sig(_signum: int, _frame: Any) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, _handle_sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _handle_sig)
        logger.info(
            "Starting worker=%s concurrency=%s page_workers=%s ocr_enabled=%s",
            runner.worker_id,
            runner.concurrency,
            runner.handler.extractor.page_workers,
            runner.handler.cache_config.ocr_enabled,
        )
        runner.run_forever(stop_event)
        return 0
    finally:
        runner.close()
        runner.db.close()


if __name__ == "__main__":
    sys.exit(main())
