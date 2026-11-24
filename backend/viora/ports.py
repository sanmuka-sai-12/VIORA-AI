from typing import Protocol

from .contracts import (
    ArtifactVersionRef,
    IdempotentRequest,
    JobAcceptance,
    TenantContext,
)


class AIProvider(Protocol):
    def complete(self, context: TenantContext, prompt: str) -> str: ...


class Storage(Protocol):
    def read(self, context: TenantContext, artifact: ArtifactVersionRef) -> bytes: ...


class Workflow(Protocol):
    def submit(
        self, request: IdempotentRequest, artifact: ArtifactVersionRef
    ) -> JobAcceptance: ...
