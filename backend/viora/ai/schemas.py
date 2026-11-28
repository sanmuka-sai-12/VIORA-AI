from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

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
class GatewayMetadata:
    provider: str = "unknown"
    latency_ms: float = 0.0
    retry_count: int = 0
    attempts: int = 1
    simulated: bool = False
    input_chars: int = 0
    output_chars: int = 0


@dataclass(frozen=True)
class CompletionResult:
    content: str
    model: str
    usage: Usage
    metadata: GatewayMetadata = field(default_factory=GatewayMetadata)
    finish_reason: str | None = None


@dataclass(frozen=True)
class StructuredResult(Generic[T]):
    parsed: T
    raw_content: str
    model: str
    usage: Usage
    metadata: GatewayMetadata = field(default_factory=GatewayMetadata)


@dataclass(frozen=True)
class EmbeddingResult:
    embeddings: list[list[float]]
    model: str
    usage: Usage
    metadata: GatewayMetadata = field(default_factory=GatewayMetadata)


class AIResourceLimits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_messages: int = Field(default=32, ge=1, le=256)
    max_input_chars: int = Field(default=64_000, ge=1, le=500_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=32_768)
    max_embedding_texts: int = Field(default=64, ge=1, le=512)
    max_embedding_chars_per_text: int = Field(default=16_000, ge=1, le=100_000)
    max_embedding_total_chars: int = Field(default=128_000, ge=1, le=1_000_000)


class AIRequestConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=300.0)
    total_deadline_seconds: float = Field(default=60.0, gt=0.0, le=600.0)
    max_retries: int = Field(default=2, ge=0, le=10)
    base_backoff_seconds: float = Field(default=0.1, ge=0.0, le=30.0)
    max_backoff_seconds: float = Field(default=2.0, ge=0.0, le=60.0)
    resource_limits: AIResourceLimits = Field(default_factory=AIResourceLimits)


class AIError(Exception):
    """Base AI gateway error that sanitizes private content, keys and prompts."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class AIConfigurationError(AIError):
    def __init__(self, message: str = "AI service configuration error") -> None:
        super().__init__("ai_configuration_error", message)


class AIResourceLimitError(AIError):
    def __init__(self, message: str = "AI request exceeds resource limits") -> None:
        super().__init__("ai_resource_limit_error", message)


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


class AIQuotaExhaustedError(AIError):
    """Non-retryable error raised when free-tier or account neuron/token quota is exhausted."""

    def __init__(
        self, message: str = "AI provider daily/account quota exhausted"
    ) -> None:
        super().__init__("ai_quota_exhausted", message)


class AITimeoutError(AITransientError):
    def __init__(self, message: str = "AI service request timed out") -> None:
        super().__init__(message, code="ai_timeout_error")


class AIResponseFormatError(AIError):
    def __init__(
        self, message: str = "Malformed or unparseable structured AI response"
    ) -> None:
        super().__init__("ai_response_format_error", message)


class AIContentFilterError(AIError):
    def __init__(self, message: str = "Content filter triggered by AI provider") -> None:
        super().__init__("ai_content_filter_error", message)


DEFAULT_TRUSTED_AI_HOSTS: frozenset[str] = frozenset(
    {
        "api.openai.com",
        "api.cloudflare.com",
        "gateway.ai.cloudflare.com",
        "viora-ai.reddyanvesh2008.workers.dev",
    }
)


def validate_provider_endpoint(
    url: str | None,
    *,
    trusted_hosts: frozenset[str] = DEFAULT_TRUSTED_AI_HOSTS,
    allow_local_http: bool = False,
) -> str | None:
    """Validate that an external AI base URL is explicitly trusted and safe."""
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned:
        raise AIConfigurationError("AI endpoint URL cannot be blank")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"https", "http"}:
        raise AIConfigurationError(
            f"Unsupported AI endpoint scheme '{parsed.scheme}'. Only HTTPS or configured local HTTP endpoints are permitted."
        )
    if not parsed.hostname:
        raise AIConfigurationError("AI endpoint URL must include a valid host")
    if parsed.username is not None or parsed.password is not None:
        raise AIConfigurationError(
            "AI endpoint URL must not include embedded credentials"
        )
    if parsed.query or parsed.fragment:
        raise AIConfigurationError(
            "AI endpoint URL must not include query parameters or fragments"
        )
    host = parsed.hostname.lower()
    if parsed.scheme == "http":
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise AIConfigurationError(
                f"Insecure HTTP AI endpoint '{cleaned}' is prohibited for non-local hosts. Use HTTPS to protect credentials and payloads."
            )
        if not allow_local_http:
            raise AIConfigurationError(
                "Local insecure HTTP AI endpoints require explicit development configuration"
            )
        return cleaned

    normalized_trusted = {h.strip().lower() for h in trusted_hosts if h.strip()}
    if host not in normalized_trusted:
        raise AIConfigurationError(
            f"Untrusted AI endpoint host '{host}'. Configure trusted AI endpoints explicitly."
        )
    return cleaned


def clamp_resource_limits(
    requested: AIResourceLimits, hard_ceiling: AIResourceLimits
) -> AIResourceLimits:
    """Enforce server/adapter upper bounds so callers cannot raise resource ceilings."""
    return AIResourceLimits(
        max_messages=min(requested.max_messages, hard_ceiling.max_messages),
        max_input_chars=min(requested.max_input_chars, hard_ceiling.max_input_chars),
        max_output_tokens=min(
            requested.max_output_tokens, hard_ceiling.max_output_tokens
        ),
        max_embedding_texts=min(
            requested.max_embedding_texts, hard_ceiling.max_embedding_texts
        ),
        max_embedding_chars_per_text=min(
            requested.max_embedding_chars_per_text,
            hard_ceiling.max_embedding_chars_per_text,
        ),
        max_embedding_total_chars=min(
            requested.max_embedding_total_chars,
            hard_ceiling.max_embedding_total_chars,
        ),
    )


def validate_chat_budget(
    messages: list[ChatMessage],
    config: AIRequestConfig,
    *,
    hard_limits: AIResourceLimits | None = None,
) -> int:
    """Enforce bounded message count, input characters, and requested output tokens."""
    ceiling = hard_limits or AIResourceLimits()
    limits = clamp_resource_limits(config.resource_limits, ceiling)
    if not messages:
        raise AIResourceLimitError("At least one chat message is required")
    if len(messages) > limits.max_messages:
        raise AIResourceLimitError(
            f"Message count {len(messages)} exceeds limit {limits.max_messages}"
        )
    total_chars = sum(len(m.content) for m in messages)
    if total_chars > limits.max_input_chars:
        raise AIResourceLimitError(
            f"Total input size {total_chars} chars exceeds limit {limits.max_input_chars}"
        )
    if (
        config.max_tokens is not None
        and config.max_tokens > limits.max_output_tokens
    ):
        raise AIResourceLimitError(
            f"Requested max_tokens {config.max_tokens} exceeds limit {limits.max_output_tokens}"
        )
    return total_chars


def validate_embedding_budget(
    texts: list[str],
    config: AIRequestConfig,
    *,
    hard_limits: AIResourceLimits | None = None,
) -> int:
    """Enforce bounded batch size and character budgets for embedding calls."""
    ceiling = hard_limits or AIResourceLimits()
    limits = clamp_resource_limits(config.resource_limits, ceiling)
    if not texts:
        raise AIResourceLimitError("At least one text is required for embedding")
    if len(texts) > limits.max_embedding_texts:
        raise AIResourceLimitError(
            f"Embedding batch size {len(texts)} exceeds limit {limits.max_embedding_texts}"
        )
    total_chars = 0
    for item in texts:
        item_len = len(item)
        if item_len > limits.max_embedding_chars_per_text:
            raise AIResourceLimitError(
                f"Embedding item length {item_len} exceeds limit {limits.max_embedding_chars_per_text}"
            )
        total_chars += item_len
    if total_chars > limits.max_embedding_total_chars:
        raise AIResourceLimitError(
            f"Total embedding size {total_chars} chars exceeds limit {limits.max_embedding_total_chars}"
        )
    return total_chars
