from typing import TypeVar

from pydantic import BaseModel

from ..contracts import TenantContext
from .ports import AIGateway
from .schemas import (
    AIRequestConfig,
    ChatMessage,
    CompletionResult,
    EmbeddingResult,
    StructuredResult,
    Usage,
)

T = TypeVar("T", bound=BaseModel)


class FakeAIGateway(AIGateway):
    """Deterministic, zero-network AI gateway implementation for unit and contract testing."""

    def __init__(
        self,
        default_completion: str = "Fake AI completion response",
        structured_payloads: dict[type[BaseModel], BaseModel] | None = None,
        embedding_dimension: int = 1536,
        model_name: str = "fake-model",
    ) -> None:
        self.default_completion = default_completion
        self.structured_payloads = structured_payloads or {}
        self.embedding_dimension = embedding_dimension
        self.model_name = model_name
        self.recorded_calls: list[dict[str, object]] = []

    def complete(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        config: AIRequestConfig | None = None,
    ) -> CompletionResult:
        self.recorded_calls.append(
            {
                "method": "complete",
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "messages": messages,
                "config": config,
            }
        )
        output_content = self.default_completion
        if config and config.max_tokens is not None:
            max_chars = config.max_tokens * 4
            if len(output_content) > max_chars:
                output_content = output_content[:max_chars]

        total_len = sum(len(m.content) for m in messages)
        usage = Usage(
            input_tokens=total_len // 4,
            output_tokens=len(output_content) // 4,
            total_tokens=(total_len + len(output_content)) // 4,
        )
        return CompletionResult(
            content=output_content,
            model=self.model_name,
            usage=usage,
        )

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model: type[T],
        config: AIRequestConfig | None = None,
    ) -> StructuredResult[T]:
        self.recorded_calls.append(
            {
                "method": "complete_structured",
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "messages": messages,
                "response_model": response_model,
                "config": config,
            }
        )
        if response_model in self.structured_payloads:
            parsed = self.structured_payloads[response_model]
            assert isinstance(parsed, response_model)
        else:
            # Construct default schema instance using schema defaults or mock values
            schema_fields = response_model.model_fields
            mock_data: dict[str, object] = {}
            for field_name, field_info in schema_fields.items():
                if field_info.default is not None:
                    mock_data[field_name] = field_info.default
                elif field_info.annotation is str:
                    mock_data[field_name] = "mock_value"
                elif field_info.annotation is int:
                    mock_data[field_name] = 1
                elif field_info.annotation is bool:
                    mock_data[field_name] = True
                elif field_info.annotation is list:
                    mock_data[field_name] = []
            parsed = response_model.model_validate(mock_data)

        raw_json = parsed.model_dump_json()
        total_len = sum(len(m.content) for m in messages)
        usage = Usage(
            input_tokens=total_len // 4,
            output_tokens=len(raw_json) // 4,
            total_tokens=(total_len + len(raw_json)) // 4,
        )
        return StructuredResult(
            parsed=parsed,
            raw_content=raw_json,
            model=self.model_name,
            usage=usage,
        )

    def embed(
        self,
        context: TenantContext,
        texts: list[str],
        config: AIRequestConfig | None = None,
    ) -> EmbeddingResult:
        self.recorded_calls.append(
            {
                "method": "embed",
                "tenant_id": context.tenant_id,
                "actor_id": context.actor_id,
                "texts": texts,
                "config": config,
            }
        )
        total_chars = sum(len(t) for t in texts)
        embeddings = [
            [0.1 * ((i + j) % 10) for j in range(self.embedding_dimension)]
            for i in range(len(texts))
        ]
        usage = Usage(
            input_tokens=total_chars // 4,
            output_tokens=0,
            total_tokens=total_chars // 4,
        )
        return EmbeddingResult(
            embeddings=embeddings,
            model=self.model_name,
            usage=usage,
        )
