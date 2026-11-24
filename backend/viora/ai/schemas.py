from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T", bound=BaseModel)


class Role(StrEnum):
    system = "system"
    user = "user"
    assistant = "assistant"


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class CompletionResult:
    content: str
    model: str
    usage: Usage


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    parsed: T
    raw_content: str
    model: str
    usage: Usage


@dataclass(frozen=True)
class EmbeddingResult:
    embeddings: list[list[float]]
    model: str
    usage: Usage


class AIRequestConfig(BaseModel):
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    max_retries: int = Field(default=2, ge=0)


class AIError(Exception):
    """Base AI gateway error that sanitizes private content, keys and prompts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AIConfigurationError(AIError):
    def __init__(self, message: str = "AI service configuration error") -> None:
        super().__init__("ai_configuration_error", message)


class AITransientError(AIError):
    def __init__(
        self,
        message: str = "Transient AI service failure, retryable",
        code: str = "ai_transient_error",
    ) -> None:
        super().__init__(code, message)


class AIRateLimitError(AITransientError):
    def __init__(self, message: str = "AI service rate limit exceeded") -> None:
        super().__init__(message, code="ai_rate_limit_error")


class AITimeoutError(AITransientError):
    def __init__(self, message: str = "AI service request timed out") -> None:
        super().__init__(message, code="ai_timeout_error")


class AIResponseFormatError(AIError):
    def __init__(self, message: str = "Malformed or unparseable structured AI response") -> None:
        super().__init__("ai_response_format_error", message)


class AIContentFilterError(AIError):
    def __init__(self, message: str = "Content filter triggered by AI provider") -> None:
        super().__init__("ai_content_filter_error", message)
