"""Durable workflow service implementing the core Workflow protocol and job controls."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from ..contracts import (
    ArtifactVersionRef,
    IdempotentRequest,
    JobAcceptance,
    Page,
    ResourceRef,
)
from ..ports import Workflow
from ..security import AccessError, TransactionalAccess
from .ports import ConcurrentJobConflict, WorkflowJobRepository
from .schemas import WorkflowJobDetail, WorkflowJobSubmit, WorkflowJobSummary


class DurableWorkflowService(Workflow):
    """Implements the Workflow port over PostgreSQL transactional job persistence."""

    def __init__(
        self,
        access: TransactionalAccess,
        repo_factory: Callable[[Any], WorkflowJobRepository],
        *,
        bearer: str | None = None,
        max_attempts: int = 3,
    ) -> None:
        self._access = access
        self._repo_factory = repo_factory
        self._bearer = bearer
        self._max_attempts = max_attempts

    def submit(
        self, request: IdempotentRequest, artifact: ArtifactVersionRef
    ) -> JobAcceptance:
        """Accept an idempotent workflow request inside a short authorized transaction."""
        resource = ResourceRef(
            tenant_id=request.tenant_id,
            kind="source",
            resource_id=artifact.resource_id,
        )
        detail = self.submit_for_source(
            bearer=self._bearer,
            resource=resource,
            action="source.extract",
            submit_req=WorkflowJobSubmit(
                tenant_id=request.tenant_id,
                offering_id=None,
                source_id=artifact.resource_id,
                version_id=artifact.version_id,
                submitted_by_user_id=request.actor_id,
                operation_kind="source_extract",
                idempotency_key=request.key,
                request_digest=request.payload_hash,
                input_artifact=artifact,
                max_attempts=self._max_attempts,
            ),
        )
        return JobAcceptance(job_id=detail.job_id)

    def submit_for_source(
        self,
        *,
        bearer: str | None,
        resource: ResourceRef,
        action: str,
        submit_req: WorkflowJobSubmit,
    ) -> WorkflowJobDetail:
        now = datetime.now(UTC)
        try:
            with self._access.transaction(bearer, action, resource) as scope:
                repo = self._repo_factory(scope.session)
                detail, is_replay = repo.enqueue(submit_req, now=now)
                if not is_replay:
                    scope.audit.change(
                        scope.context,
                        "workflow.job.queued",
                        ResourceRef(
                            tenant_id=submit_req.tenant_id,
                            kind="workflow_job",
                            resource_id=detail.job_id,
                        ),
                    )
                return detail
        except ConcurrentJobConflict:
            with self._access.transaction(bearer, action, resource) as replay_scope:
                repo = self._repo_factory(replay_scope.session)
                if submit_req.idempotency_key is not None:
                    found = repo.lookup_idempotency(
                        tenant_id=submit_req.tenant_id,
                        actor_id=submit_req.submitted_by_user_id,
                        operation_kind=submit_req.operation_kind,
                        idempotency_key=submit_req.idempotency_key,
                        request_digest=submit_req.request_digest,
                    )
                    if found is not None:
                        return found
                active = repo.find_active_for_version(
                    tenant_id=submit_req.tenant_id,
                    version_id=submit_req.version_id,
                    operation_kind=submit_req.operation_kind,
                )
                if active is not None:
                    return active
                raise AccessError(
                    409,
                    "job_conflict",
                    "Concurrent job registration conflict",
                ) from None

    @staticmethod
    def get_job(
        access: TransactionalAccess,
        repo_factory: Callable[[Any], WorkflowJobRepository],
        *,
        bearer: str | None,
        tenant_id: UUID,
        job_id: UUID,
    ) -> WorkflowJobDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="workflow_job", resource_id=job_id)
        with access.transaction(bearer, "source.job.read", ref) as scope:
            found = repo_factory(scope.session).get_job(tenant_id, job_id)
            if found is None:
                raise AccessError(404, "job_missing", "Workflow job unavailable")
            return found

    @staticmethod
    def list_source_jobs(
        access: TransactionalAccess,
        repo_factory: Callable[[Any], WorkflowJobRepository],
        *,
        bearer: str | None,
        tenant_id: UUID,
        source_id: UUID,
        limit: int = 25,
    ) -> Page[WorkflowJobSummary]:
        ref = ResourceRef(tenant_id=tenant_id, kind="source", resource_id=source_id)
        with access.transaction(bearer, "source.job.read", ref) as scope:
            return repo_factory(scope.session).list_source_jobs(
                tenant_id, source_id, limit=limit
            )

    @staticmethod
    def cancel_job(
        access: TransactionalAccess,
        repo_factory: Callable[[Any], WorkflowJobRepository],
        *,
        bearer: str | None,
        tenant_id: UUID,
        job_id: UUID,
    ) -> WorkflowJobDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="workflow_job", resource_id=job_id)
        with access.transaction(bearer, "source.job.cancel", ref) as scope:
            scope.recheck("source.job.cancel", ref)
            updated = repo_factory(scope.session).request_cancel(
                tenant_id, job_id, now=datetime.now(UTC)
            )
            if updated is None:
                raise AccessError(404, "job_missing", "Workflow job unavailable")
            scope.audit.change(scope.context, "workflow.job.cancel_requested", ref)
            return updated

    @staticmethod
    def retry_job(
        access: TransactionalAccess,
        repo_factory: Callable[[Any], WorkflowJobRepository],
        *,
        bearer: str | None,
        tenant_id: UUID,
        job_id: UUID,
    ) -> WorkflowJobDetail:
        ref = ResourceRef(tenant_id=tenant_id, kind="workflow_job", resource_id=job_id)
        with access.transaction(bearer, "source.job.retry", ref) as scope:
            scope.recheck("source.job.retry", ref)
            updated = repo_factory(scope.session).request_retry(
                tenant_id, job_id, now=datetime.now(UTC)
            )
            if updated is None:
                raise AccessError(404, "job_missing", "Workflow job unavailable")
            scope.audit.change(scope.context, "workflow.job.retried", ref)
            return updated
