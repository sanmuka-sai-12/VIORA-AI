"""Pydantic contracts for durable workflow jobs, leases, progress, and results."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from ..contracts import ArtifactVersionRef, Contract, JobState

WorkflowOperationKind = Literal["source_extract", "concept_extract", "knowledge_index"]


class WorkflowJobSubmit(Contract):
    tenant_id: UUID
    offering_id: UUID | None = None
    source_id: UUID
    version_id: UUID
    submitted_by_user_id: UUID
    operation_kind: WorkflowOperationKind = "source_extract"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    request_digest: str | None = None
    input_artifact: ArtifactVersionRef
    max_attempts: int = Field(default=3, ge=1, le=10)


class WorkflowJobSummary(Contract):
    job_id: UUID
    tenant_id: UUID
    offering_id: UUID | None = None
    source_id: UUID
    version_id: UUID
    submitted_by_user_id: UUID
    operation_kind: WorkflowOperationKind
    state: JobState
    stage: str
    progress_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    next_run_at: datetime
    cancel_requested: bool = False
    result_extraction_id: UUID | None = None
    result_metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)
    last_error_code: str | None = None
    last_error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class WorkflowJobDetail(WorkflowJobSummary):
    fencing_token: int = Field(ge=0)
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    input_artifact: ArtifactVersionRef
    idempotent_replay: bool = False


class ClaimedWorkflowJob(Contract):
    """Internal worker claim payload carrying exact fencing token and input artifact ref."""

    job_id: UUID
    tenant_id: UUID
    offering_id: UUID | None = None
    source_id: UUID
    version_id: UUID
    submitted_by_user_id: UUID
    operation_kind: WorkflowOperationKind
    attempt_count: int = Field(ge=1)
    max_attempts: int = Field(ge=1)
    fencing_token: int = Field(ge=1)
    lease_owner: str
    lease_expires_at: datetime
    input_artifact: ArtifactVersionRef
    checkpoint_data: dict[str, object] | None = None
    created_at: datetime
    started_at: datetime


class LeaseRenewalResult(Contract):
    renewed: bool
    cancel_requested: bool


class ExtractionTriggerRequest(Contract):
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
