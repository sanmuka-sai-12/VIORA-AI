from typing import Protocol, TypeVar

from pydantic import BaseModel

from ..contracts import TenantContext
from .schemas import (
    AIRequestConfig,
    ChatMessage,
    CompletionResult,
    EmbeddingResult,
    StructuredResult,
)

T = TypeVar("T", bound=BaseModel)


class AIGateway(Protocol):
    """Provider-independent gateway for text generation, structured parsing and embeddings."""

    def complete(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        config: AIRequestConfig | None = None,
    ) -> CompletionResult: ...

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model: type[T],
        config: AIRequestConfig | None = None,
    ) -> StructuredResult[T]: ...

    def embed(
        self,
        context: TenantContext,
        texts: list[str],
        config: AIRequestConfig | None = None,
    ) -> EmbeddingResult: ...
