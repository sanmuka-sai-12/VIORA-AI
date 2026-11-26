"""Ports and errors for durable workflow job execution and worker fencing."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from ..contracts import Page
from .schemas import (
    ClaimedWorkflowJob,
    LeaseRenewalResult,
    WorkflowJobDetail,
    WorkflowJobSubmit,
    WorkflowJobSummary,
)


class StaleWorkerFenceError(RuntimeError):
    """Raised when a worker attempts to renew, checkpoint, or finalize after losing its lease."""


class JobCancelledError(RuntimeError):
    """Raised when cooperative cancellation is detected during execution."""


class ConcurrentJobConflict(Exception):
    """Raised on 23505 unique conflict when concurrent job submissions race."""


class WorkflowJobRepository(Protocol):
    def lookup_idempotency(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        operation_kind: str,
        idempotency_key: str,
        request_digest: str | None,
    ) -> WorkflowJobDetail | None: ...

    def find_active_for_version(
        self,
        *,
        tenant_id: UUID,
        version_id: UUID,
        operation_kind: str,
    ) -> WorkflowJobDetail | None: ...

    def enqueue(
        self, request: WorkflowJobSubmit, *, now: datetime
    ) -> tuple[WorkflowJobDetail, bool]: ...

    def get_job(self, tenant_id: UUID, job_id: UUID) -> WorkflowJobDetail | None: ...

    def list_source_jobs(
        self,
        tenant_id: UUID,
        source_id: UUID,
        *,
        limit: int,
    ) -> Page[WorkflowJobSummary]: ...

    def request_cancel(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> WorkflowJobDetail | None: ...

    def request_retry(
        self, tenant_id: UUID, job_id: UUID, *, now: datetime
    ) -> WorkflowJobDetail | None: ...


class JobExecutionHandler(Protocol):
    """Executes one claimed job outside any open database transaction."""

    def execute(
        self,
        job: ClaimedWorkflowJob,
        *,
        heartbeat: "WorkerLeaseController",
    ) -> tuple[UUID, dict[str, str | int | float | bool]]: ...


class WorkerLeaseController(Protocol):
    """Allows long-running CPU extraction to renew lease, persist checkpoints, and observe cancellation."""

    def renew(
        self,
        *,
        stage: str,
        progress_metadata: dict[str, str | int | float | bool],
        checkpoint_data: dict[str, object] | None = None,
    ) -> LeaseRenewalResult: ...

    def check_cancelled(self) -> bool: ...
