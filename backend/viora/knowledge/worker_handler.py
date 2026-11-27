"""Durable background worker handler for grounded AI educational concept extraction."""

from __future__ import annotations

import contextlib
import hashlib
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

from ..ai.public import AIGateway, AIRequestConfig
from ..authorization.public import AuthorizationService
from ..contracts import Actor, ResourceRef, TenantContext
from ..db import Database
from ..sources.public import DerivedExtractionRecord, SqlSourceRepository
from ..storage.public import ArtifactStore
from ..workflow.public import (
    ClaimedWorkflowJob,
    JobCancelledError,
    SqlWorkflowWorkerStore,
    StaleWorkerFenceError,
    WorkerLeaseController,
)
from .blueprint import LessonBlueprintGenerator
from .blueprint_store import (
    SqlLessonBlueprintRepository,
    build_blueprint_input_fingerprint,
)
from .curriculum import generate_lesson_outline
from .extractor import ConceptExtractor
from .publication import publication_complete, reviewed_graph
from .repository import (
    SqlConceptExtractionCache,
    SqlConceptExtractionRepository,
    build_concept_cache_key,
    build_concept_input_fingerprint,
    build_knowledge_scope_key,
    rebind_cached_knowledge_graph,
)
from .schemas import (
    BLUEPRINT_PROMPT_VERSION,
    BLUEPRINT_SCHEMA_VERSION,
    CHUNK_ALGORITHM_VERSION,
    CONCEPT_SCHEMA_VERSION,
    PROMPT_VERSION,
    ChunkingConfig,
    ChunkingMode,
)


class ConceptExtractionWorkerHandler:
    """Executes concept_extract jobs with reauthorization, caching, and fenced commits."""

    def __init__(
        self,
        *,
        db: Database,
        access: AuthorizationService,
        gateway: AIGateway | None,
        cache_ttl_seconds: int = 7 * 24 * 3600,
        heartbeat_seconds: int = 5,
        chunk_config: ChunkingConfig | None = None,
        request_config: AIRequestConfig | None = None,
    ) -> None:
        self.db = db
        self.access = access
        self.gateway = gateway
        self.cache_ttl_seconds = cache_ttl_seconds
        self.heartbeat_seconds = max(1, heartbeat_seconds)
        self.chunk_config = chunk_config or ChunkingConfig()
        self.request_config = request_config

    def _provider_and_model(self) -> tuple[str, str]:
        if self.gateway is None:
            return "unconfigured", "unconfigured"
        provider = getattr(self.gateway, "provider_name", None)
        if not isinstance(provider, str) or not provider.strip():
            provider = "fake" if self.gateway.__class__.__name__.startswith("Fake") else "ai"
        model = getattr(
            self.gateway,
            "default_model",
            getattr(self.gateway, "model_name", "unknown"),
        )
        return str(provider), str(model)

    def execute(
        self,
        job: ClaimedWorkflowJob,
        *,
        heartbeat: WorkerLeaseController,
        force_mode: ChunkingMode | None = None,
    ) -> tuple[UUID, dict[str, str | int | float | bool]]:
        from ..ai.public import AIConfigurationError

        if self.gateway is None:
            raise AIConfigurationError(
                "No AI provider configured for concept_extract job (configure AI_PROVIDER and credentials)"
            )
        provider_name, model_name = self._provider_and_model()
        actor = Actor(actor_id=job.submitted_by_user_id)
        source_ref = ResourceRef(
            tenant_id=job.tenant_id, kind="source", resource_id=job.source_id
        )

        # Stage 1: Reauthorize + load immutable derived extraction + check scoped AI cache (short TX)
        heartbeat.renew(stage="reauthorizing", progress_metadata={"stage": "reauthorizing"})
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before concept reauthorization")

        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            src_repo = SqlSourceRepository(scope.session)
            src_info = src_repo.get_source_access(job.tenant_id, job.source_id)
            derived: DerivedExtractionRecord | None = (
                src_repo.get_latest_derived_extraction(
                    job.tenant_id, job.source_id, job.version_id
                )
            )
            if src_info is None or derived is None:
                from ..security import AccessError

                raise AccessError(
                    409,
                    "derived_extraction_missing",
                    "Derived document extraction must exist before concept extraction",
                )
            scope_key = build_knowledge_scope_key(
                offering_id=src_info.offering_id,
                origin_kind=src_info.origin_kind,
                owner_user_id=src_info.owner_user_id,
            )
            chunk_cfg_digest = hashlib.sha256(
                self.chunk_config.model_dump_json().encode("utf-8")
            ).hexdigest()
            fingerprint = build_concept_input_fingerprint(
                derived=derived,
                provider=provider_name,
                model=model_name,
                prompt_version=PROMPT_VERSION,
                schema_version=CONCEPT_SCHEMA_VERSION,
                chunk_algorithm_version=CHUNK_ALGORITHM_VERSION,
                chunk_config_digest=chunk_cfg_digest,
            )
            cache_key = build_concept_cache_key(
                scope_key=scope_key,
                input_fingerprint=fingerprint,
            )
            cached = SqlConceptExtractionCache(scope.session).lookup(
                tenant_id=job.tenant_id,
                cache_key=cache_key,
                scope_key=scope_key,
                input_fingerprint=fingerprint,
                now=datetime.now(UTC),
            )
            tenant_ctx: TenantContext = scope.context

        # Stage 2: Execute chunking + AI concept extraction OUTSIDE any DB transaction
        if cached is not None:
            cached_graph, in_tok, out_tok, tot_tok, inf_reqs = cached
            cache_hit = True
            knowledge = rebind_cached_knowledge_graph(
                cached_graph,
                source_id=job.source_id,
                version_id=job.version_id,
                derived_extraction_id=derived.extraction_id,
                source_sha256=derived.source_sha256,
                title=derived.document.title,
                created_at=datetime.now(UTC),
            )
            chunking_ms = 0.0
            inference_ms = 0.0
            validation_ms = 0.0
            heartbeat.renew(
                stage="cache_hit",
                progress_metadata={"stage": "cache_hit", "cache_hit": True},
            )
        else:
            cache_hit = False
            heartbeat.renew(
                stage="chunking_and_extracting",
                progress_metadata={"stage": "chunking_and_extracting", "cache_hit": False},
            )
            stop_hb = threading.Event()

            def _hb_loop() -> None:
                while not stop_hb.wait(self.heartbeat_seconds):
                    try:
                        res = heartbeat.renew(
                            stage="ai_concept_extraction",
                            progress_metadata={
                                "stage": "ai_concept_extraction",
                                "provider": provider_name,
                                "model": model_name,
                            },
                        )
                        if not res.renewed or res.cancel_requested:
                            break
                    except Exception:  # noqa: BLE001
                        break

            extractor = ConceptExtractor(
                self.gateway,
                chunk_config=self.chunk_config,
                request_config=self.request_config,
            )
            hb_thread = threading.Thread(target=_hb_loop, daemon=True)
            hb_thread.start()
            t0 = time.monotonic_ns()
            try:
                if heartbeat.check_cancelled():
                    raise JobCancelledError("Cancelled before concept extraction")
                result = extractor.extract_concepts(
                    context=tenant_ctx,
                    derived=derived,
                    force_mode=force_mode,
                    cancel_check=heartbeat.check_cancelled,
                )
            except InterruptedError:
                raise JobCancelledError("Concept extraction cancelled") from None
            finally:
                stop_hb.set()
                with contextlib.suppress(Exception):
                    hb_thread.join(timeout=1.0)
            _ = t0
            knowledge = result.knowledge
            provider_name = result.provider
            model_name = result.model
            in_tok = result.input_tokens
            out_tok = result.output_tokens
            tot_tok = result.total_tokens
            inf_reqs = result.inference_requests
            chunking_ms = result.chunking_ms
            inference_ms = result.inference_ms
            validation_ms = result.validation_ms

        if getattr(heartbeat, "lease_lost", False):
            raise StaleWorkerFenceError("Lease lost during AI concept extraction")
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled during AI concept extraction")

        result_meta: dict[str, str | int | float | bool] = {
            "concept_count": len(knowledge.concepts),
            "relationship_count": len(knowledge.relationships),
            "source_supported_count": knowledge.validation.source_supported_count,
            "learning_request_count": knowledge.validation.learning_request_count,
            "uncertain_count": knowledge.validation.uncertain_count,
            "unsupported_count": knowledge.validation.unsupported_count,
            "provider": provider_name,
            "model": model_name,
            "total_tokens": tot_tok,
            "inference_requests": inf_reqs,
            "cache_hit": cache_hit,
            "published_to_students": False,
        }

        # Stage 3: Reauthorize actor/source + persist concept_extractions + scoped cache + fenced commit (short TX)
        now = datetime.now(UTC)
        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            c_repo = SqlConceptExtractionRepository(scope.session)
            rec = c_repo.append_concept_extraction(
                tenant_id=job.tenant_id,
                source_id=job.source_id,
                version_id=job.version_id,
                derived_extraction_id=derived.extraction_id,
                extraction_version=derived.extraction_version,
                job_id=job.job_id,
                provider=provider_name,
                model=model_name,
                prompt_version=PROMPT_VERSION,
                schema_version=CONCEPT_SCHEMA_VERSION,
                chunk_algorithm_version=CHUNK_ALGORITHM_VERSION,
                input_fingerprint=fingerprint,
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_tokens=tot_tok,
                inference_requests=inf_reqs,
                chunking_ms=chunking_ms,
                inference_ms=inference_ms,
                validation_ms=validation_ms,
                cache_hit=cache_hit,
                cache_key=cache_key,
                knowledge=knowledge,
                now=now,
            )
            if not cache_hit and knowledge.validation.total_concepts > 0:
                SqlConceptExtractionCache(scope.session).put(
                    tenant_id=job.tenant_id,
                    cache_key=cache_key,
                    scope_key=scope_key,
                    input_fingerprint=fingerprint,
                    provider=provider_name,
                    model=model_name,
                    prompt_version=PROMPT_VERSION,
                    schema_version=CONCEPT_SCHEMA_VERSION,
                    chunk_algorithm_version=CHUNK_ALGORITHM_VERSION,
                    source_id=job.source_id,
                    version_id=job.version_id,
                    derived_extraction_id=derived.extraction_id,
                    concept_extraction_id=rec.concept_extraction_id,
                    knowledge=knowledge,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_tokens=tot_tok,
                    inference_requests=inf_reqs,
                    ttl_seconds=self.cache_ttl_seconds,
                    now=now,
                )
            SqlWorkflowWorkerStore.finalize_succeeded(
                scope.session,
                tenant_id=job.tenant_id,
                job_id=job.job_id,
                worker_id=job.lease_owner,
                fencing_token=job.fencing_token,
                extraction_id=rec.concept_extraction_id,
                result_metadata=result_meta,
                progress_metadata={**result_meta, "stage": "completed"},
                now=now,
            )
            scope.audit.change(
                scope.context,
                "workflow.concept_extract.succeeded",
                ResourceRef(
                    tenant_id=job.tenant_id,
                    kind="workflow_job",
                    resource_id=job.job_id,
                ),
            )
        return rec.concept_extraction_id, result_meta


class LessonBlueprintWorkerHandler:
    """Executes lesson_blueprint jobs with pre/post authorization, ArtifactStore, and fenced commits."""

    def __init__(
        self,
        *,
        db: Database,
        access: AuthorizationService,
        artifact_store: ArtifactStore,
        gateway: AIGateway | None,
        heartbeat_seconds: int = 5,
        request_config: AIRequestConfig | None = None,
    ) -> None:
        self.db = db
        self.access = access
        self.artifact_store = artifact_store
        self.gateway = gateway
        self.heartbeat_seconds = max(1, heartbeat_seconds)
        self.request_config = request_config

    def execute(
        self,
        job: ClaimedWorkflowJob,
        *,
        heartbeat: WorkerLeaseController,
    ) -> tuple[UUID, dict[str, str | int | float | bool]]:
        from ..ai.public import AIConfigurationError
        from ..security import AccessError

        if self.gateway is None:
            raise AIConfigurationError(
                "No AI provider configured for lesson_blueprint job (configure AI_PROVIDER and credentials)"
            )
        provider_name = getattr(self.gateway, "provider_name", None)
        if not isinstance(provider_name, str) or not provider_name.strip():
            provider_name = (
                "fake" if self.gateway.__class__.__name__.startswith("Fake") else "ai"
            )
        model_name = str(
            getattr(
                self.gateway,
                "default_model",
                getattr(self.gateway, "model_name", "unknown"),
            )
        )
        actor = Actor(actor_id=job.submitted_by_user_id)
        source_ref = ResourceRef(
            tenant_id=job.tenant_id, kind="source", resource_id=job.source_id
        )

        heartbeat.renew(stage="reauthorizing", progress_metadata={"stage": "reauthorizing"})
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before blueprint reauthorization")

        # Stage 1: Reauthorize + verify active source & approved curriculum (short TX)
        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            src_repo = SqlSourceRepository(scope.session)
            src_info = src_repo.get_source_access(job.tenant_id, job.source_id)
            derived = src_repo.get_latest_derived_extraction(
                job.tenant_id, job.source_id, job.version_id
            )
            c_repo = SqlConceptExtractionRepository(scope.session)
            pub = c_repo.get_publication(job.tenant_id, job.version_id)
            if (
                src_info is None
                or src_info.lifecycle_state != "active"
                or src_info.origin_kind != "teacher_resource"
                or derived is None
            ):
                raise AccessError(
                    409,
                    "source_not_ready",
                    "Active teacher resource and derived extraction required for lesson blueprint",
                )
            if (
                pub is None
                or not pub.teacher_approved
                or pub.approved_review_id is None
            ):
                raise AccessError(
                    409,
                    "teacher_approval_required",
                    "Approved curriculum is required before generating a lesson blueprint",
                )
            ext = c_repo.get_by_id(job.tenant_id, pub.concept_extraction_id)
            review = c_repo.get_approved_review(pub)
            if (
                ext is None
                or review is None
                or ext.source_id != job.source_id
                or ext.version_id != job.version_id
                or ext.derived_extraction_id != derived.extraction_id
                or ext.knowledge.source_sha256 != pub.source_sha256
            ):
                raise AccessError(
                    409,
                    "approved_curriculum_mismatch",
                    "Approved curriculum records are incomplete or mismatched",
                )
            reviewed = reviewed_graph(ext.knowledge, review.rejected_concept_ids)
            if not publication_complete(reviewed):
                raise AccessError(
                    409,
                    "incomplete_publication",
                    "A complete approved curriculum is required for blueprint generation",
                )
            outline = generate_lesson_outline(reviewed)
            tenant_ctx = scope.context
            expected_review_id = pub.approved_review_id
            expected_ce_id = ext.concept_extraction_id
            expected_sha = pub.source_sha256

        # Stage 2: Generate & validate blueprint OUTSIDE any DB transaction
        heartbeat.renew(
            stage="generating_blueprint",
            progress_metadata={"stage": "generating_blueprint"},
        )
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled before blueprint AI generation")

        stop_hb = threading.Event()

        def _hb_loop() -> None:
            while not stop_hb.wait(self.heartbeat_seconds):
                try:
                    res = heartbeat.renew(
                        stage="ai_lesson_blueprint",
                        progress_metadata={
                            "stage": "ai_lesson_blueprint",
                            "provider": str(provider_name),
                            "model": model_name,
                        },
                    )
                    if not res.renewed or res.cancel_requested:
                        break
                except Exception:  # noqa: BLE001
                    break

        hb_thread = threading.Thread(target=_hb_loop, daemon=True)
        hb_thread.start()
        try:
            gen_res = LessonBlueprintGenerator(
                self.gateway, request_config=self.request_config
            ).generate(
                context=tenant_ctx,
                derived=derived,
                reviewed=reviewed,
                outline=outline,
                approved_review=review,
            )
        finally:
            stop_hb.set()
            with contextlib.suppress(Exception):
                hb_thread.join(timeout=1.0)

        if getattr(heartbeat, "lease_lost", False):
            raise StaleWorkerFenceError("Lease lost during lesson blueprint generation")
        if heartbeat.check_cancelled():
            raise JobCancelledError("Cancelled during lesson blueprint generation")

        bp = gen_res.blueprint
        fingerprint = build_blueprint_input_fingerprint(
            concept_extraction_id=expected_ce_id,
            approved_review_id=expected_review_id,
            source_sha256=expected_sha,
            outline_version=outline.outline_version,
            provider=gen_res.provider,
            model=gen_res.model,
            prompt_version=BLUEPRINT_PROMPT_VERSION,
            schema_version=BLUEPRINT_SCHEMA_VERSION,
        )
        result_meta: dict[str, str | int | float | bool] = {
            "objectives_count": len(bp.learning_objectives),
            "explanations_count": len(bp.explanations),
            "worked_examples_count": len(bp.worked_examples),
            "scenes_count": len(bp.scenes),
            "narrations_count": len(bp.narrations),
            "assessments_count": len(bp.assessments),
            "rejected_scenes_count": bp.validation.rejected_scenes_count,
            "rejected_assessment_items_count": bp.validation.rejected_assessment_items_count,
            "provider": gen_res.provider,
            "model": gen_res.model,
            "total_tokens": gen_res.total_tokens,
            "inference_requests": gen_res.inference_requests,
        }

        # Stage 3: Post-inference reauthorization + curriculum invariance check + fenced ArtifactStore commit
        now = datetime.now(UTC)
        with self.access.reauthorize(actor, "source.process", source_ref) as scope:
            src_info_after = SqlSourceRepository(scope.session).get_source_access(
                job.tenant_id, job.source_id
            )
            c_repo_after = SqlConceptExtractionRepository(scope.session)
            pub_after = c_repo_after.get_publication(job.tenant_id, job.version_id)
            rev_after = (
                c_repo_after.get_approved_review(pub_after)
                if pub_after is not None
                else None
            )
            if (
                src_info_after is None
                or src_info_after.lifecycle_state != "active"
                or pub_after is None
                or not pub_after.teacher_approved
                or pub_after.approved_review_id != expected_review_id
                or pub_after.concept_extraction_id != expected_ce_id
                or pub_after.source_sha256 != expected_sha
                or rev_after is None
            ):
                raise AccessError(
                    409,
                    "curriculum_changed_during_generation",
                    "Approved curriculum or source changed during blueprint generation",
                )
            bp_repo = SqlLessonBlueprintRepository(scope.session)
            already_stored = bp_repo.get_for_job(
                job.tenant_id,
                job.job_id,
                artifact_store=self.artifact_store,
                context=scope.context,
            )
            rec = (
                already_stored
                if already_stored is not None
                else bp_repo.append_blueprint(
                    context=scope.context,
                    artifact_store=self.artifact_store,
                    source_id=job.source_id,
                    version_id=job.version_id,
                    derived_extraction_id=derived.extraction_id,
                    concept_extraction_id=expected_ce_id,
                    approved_review_id=expected_review_id,
                    job_id=job.job_id,
                    provider=gen_res.provider,
                    model=gen_res.model,
                    prompt_version=BLUEPRINT_PROMPT_VERSION,
                    schema_version=BLUEPRINT_SCHEMA_VERSION,
                    outline_version=outline.outline_version,
                    input_fingerprint=fingerprint,
                    blueprint=bp,
                    input_tokens=gen_res.input_tokens,
                    output_tokens=gen_res.output_tokens,
                    total_tokens=gen_res.total_tokens,
                    inference_requests=gen_res.inference_requests,
                    generation_ms=gen_res.generation_ms,
                    validation_ms=gen_res.validation_ms,
                    now=now,
                )
            )
            try:
                SqlWorkflowWorkerStore.finalize_succeeded(
                    scope.session,
                    tenant_id=job.tenant_id,
                    job_id=job.job_id,
                    worker_id=job.lease_owner,
                    fencing_token=job.fencing_token,
                    extraction_id=rec.blueprint_id,
                    result_metadata=result_meta,
                    progress_metadata={**result_meta, "stage": "completed"},
                    now=now,
                )
            except Exception:
                if already_stored is None:
                    self.artifact_store.delete_uncommitted(scope.context, rec.artifact_ref)
                raise
            scope.audit.change(
                scope.context,
                "workflow.lesson_blueprint.succeeded",
                ResourceRef(
                    tenant_id=job.tenant_id,
                    kind="workflow_job",
                    resource_id=job.job_id,
                ),
            )
        return rec.blueprint_id, result_meta
