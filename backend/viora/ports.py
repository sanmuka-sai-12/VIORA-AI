from typing import TYPE_CHECKING, Protocol

from .contracts import (
    ArtifactVersionRef,
    IdempotentRequest,
    JobAcceptance,
    TenantContext,
)

if TYPE_CHECKING:
    from .ai.public import AIGateway


class AIProvider(Protocol):
    def complete(self, context: TenantContext, prompt: str) -> str: ...


class AIProviderGatewayAdapter:
    """Adapts an AIGateway instance to the legacy/simple AIProvider protocol."""

    def __init__(self, gateway: "AIGateway") -> None:
        self.gateway = gateway

    def complete(self, context: TenantContext, prompt: str) -> str:
        from .ai.public import ChatMessage, Role

        result = self.gateway.complete(
            context=context,
            messages=[ChatMessage(role=Role.user, content=prompt)],
        )
        return result.content


class Storage(Protocol):
    def read(self, context: TenantContext, artifact: ArtifactVersionRef) -> bytes: ...


class Workflow(Protocol):
    def submit(
        self, request: IdempotentRequest, artifact: ArtifactVersionRef
    ) -> JobAcceptance: ...
