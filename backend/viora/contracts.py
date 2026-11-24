from enum import StrEnum
from typing import Annotated, Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Actor(Contract):
    actor_id: UUID


class TenantContext(Actor):
    tenant_id: UUID
    roles: frozenset[str]


class ResourceRef(Contract):
    tenant_id: UUID
    kind: str
    resource_id: UUID


Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ArtifactVersionRef(ResourceRef):
    version_id: UUID
    sha256: Digest


class SourceEvidence(Contract):
    source: ArtifactVersionRef
    section_id: UUID
    page: int = Field(ge=1)


T = TypeVar("T")


class Page(Contract, Generic[T]):
    items: tuple[T, ...]
    next_cursor: str | None = None


class JobState(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    retry_wait = "retry_wait"
    failed = "failed"
    cancelled = "cancelled"


class JobAcceptance(Contract):
    job_id: UUID


class JobStatus(JobAcceptance):
    state: JobState


class IdempotencyScope(Actor):
    """Uniquely constrain this scope. Replay matching payload digests or return 409."""

    tenant_id: UUID
    operation: str
    key: str = Field(min_length=1, max_length=128)


class IdempotentRequest(IdempotencyScope):
    payload_hash: Digest


class ErrorResponse(Contract):
    code: str
    message: str
    request_id: UUID
