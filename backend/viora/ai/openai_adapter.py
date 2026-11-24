import json
import logging
from collections.abc import Callable
from typing import Any, TypeVar
from urllib.parse import urlparse

import openai
from openai import OpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from ..contracts import TenantContext
from .ports import AIGateway
from .schemas import (
    AIConfigurationError,
    AIContentFilterError,
    AIError,
    AIRateLimitError,
    AIRequestConfig,
    AIResponseFormatError,
    AITimeoutError,
    AITransientError,
    ChatMessage,
    CompletionResult,
    EmbeddingResult,
    StructuredResult,
    Usage,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class OpenAIAdapter(AIGateway):
    """
    OpenAI-compatible AI gateway adapter.
    Disables internal SDK retries (max_retries=0) to prevent double/nested retry storms,
    relying exclusively on explicit bounded retry control.
    """

    def __init__(
        self,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini",
        default_embedding_model: str = "text-embedding-3-small",
        client: OpenAI | None = None,
    ) -> None:
        self._validate_endpoint(base_url)
        self.default_model = default_model
        self.default_embedding_model = default_embedding_model
        self._api_key = api_key
        self._base_url = base_url
        if client is not None:
            self.client = client
        else:
            raw_key = api_key.get_secret_value().strip() if api_key else ""
            if not raw_key:
                raise AIConfigurationError(
                    "OpenAI API key must be provided when no custom client is supplied"
                )
            # Explicitly set max_retries=0 to avoid overlapping retry policies
            self.client = OpenAI(
                api_key=raw_key,
                base_url=base_url,
                max_retries=0,
            )

    def __repr__(self) -> str:
        return f"<OpenAIAdapter model={self.default_model!r} base_url={self._base_url!r}>"

    @staticmethod
    def _validate_endpoint(url: str | None) -> None:
        if not url:
            return
        parsed = urlparse(url)
        if parsed.scheme not in {"https", "http"}:
            raise AIConfigurationError(
                f"Unsupported AI endpoint scheme '{parsed.scheme}'. Only HTTPS or local HTTP endpoints are permitted."
            )
        if parsed.scheme == "http":
            host = (parsed.hostname or "").lower()
            if host not in {"localhost", "127.0.0.1", "::1"}:
                raise AIConfigurationError(
                    f"Insecure HTTP AI endpoint '{url}' is prohibited for non-local hosts. Use HTTPS to protect credentials and payloads."
                )

    def _execute_with_retry(
        self, operation_name: str, config: AIRequestConfig, call: Callable[[], Any]
    ) -> Any:
        retries = 0
        max_retries = config.max_retries

        while True:
            try:
                return call()
            except openai.AuthenticationError:
                logger.error("AI gateway authentication failure on %s", operation_name)
                raise AIConfigurationError("Invalid or missing AI credentials") from None
            except openai.RateLimitError:
                if retries < max_retries:
                    retries += 1
                    logger.warning("AI rate limit hit, retry %d of %d", retries, max_retries)
                    continue
                raise AIRateLimitError("AI provider rate limit exceeded") from None
            except (openai.APITimeoutError, TimeoutError):
                if retries < max_retries:
                    retries += 1
                    logger.warning("AI request timed out, retry %d of %d", retries, max_retries)
                    continue
                raise AITimeoutError("AI provider request timed out") from None
            except (openai.APIConnectionError, openai.InternalServerError):
                if retries < max_retries:
                    retries += 1
                    logger.warning("AI transient connection/server error, retry %d of %d", retries, max_retries)
                    continue
                raise AITransientError("AI provider unavailable") from None
            except openai.BadRequestError as exc:
                # Detect content filter rejections
                err_code = getattr(exc, "code", "")
                if err_code == "content_filter":
                    raise AIContentFilterError("Content rejected by safety filters") from None
                logger.error("AI bad request on %s (code=%s)", operation_name, err_code or "unknown")
                raise AIError("ai_bad_request", "AI request rejected") from None
            except openai.OpenAIError:
                logger.error("Unhandled OpenAI provider error on %s", operation_name)
                raise AIError("ai_provider_error", "AI service failure") from None

    def complete(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        config: AIRequestConfig | None = None,
    ) -> CompletionResult:
        cfg = config or AIRequestConfig()
        formatted_messages = [{"role": m.role.value, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": self.default_model,
            "messages": formatted_messages,
            "timeout": cfg.timeout_seconds,
        }
        if cfg.max_tokens is not None:
            kwargs["max_tokens"] = cfg.max_tokens
        if cfg.temperature is not None:
            kwargs["temperature"] = cfg.temperature

        response = self._execute_with_retry(
            "complete", cfg, lambda: self.client.chat.completions.create(**kwargs)
        )

        choice = response.choices[0]
        content = choice.message.content or ""
        u = response.usage
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            total_tokens=getattr(u, "total_tokens", 0) if u else 0,
        )
        return CompletionResult(
            content=content,
            model=response.model or self.default_model,
            usage=usage,
        )

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model: type[T],
        config: AIRequestConfig | None = None,
    ) -> StructuredResult[T]:
        cfg = config or AIRequestConfig()
        formatted_messages = [{"role": m.role.value, "content": m.content} for m in messages]

        kwargs: dict[str, Any] = {
            "model": self.default_model,
            "messages": formatted_messages,
            "timeout": cfg.timeout_seconds,
            "response_format": {"type": "json_object"},
        }
        if cfg.max_tokens is not None:
            kwargs["max_tokens"] = cfg.max_tokens
        if cfg.temperature is not None:
            kwargs["temperature"] = cfg.temperature

        # Append schema requirement to prompt system instructions for deterministic parsing
        schema_prompt = (
            f"\nStrict response requirement: respond in valid JSON conforming to the following schema:\n"
            f"{json.dumps(response_model.model_json_schema())}"
        )
        if formatted_messages and formatted_messages[0]["role"] == "system":
            formatted_messages[0]["content"] += schema_prompt
        else:
            formatted_messages.insert(0, {"role": "system", "content": schema_prompt.strip()})

        response = self._execute_with_retry(
            "complete_structured", cfg, lambda: self.client.chat.completions.create(**kwargs)
        )

        raw_content = response.choices[0].message.content or ""
        try:
            parsed = response_model.model_validate_json(raw_content)
        except (ValidationError, json.JSONDecodeError):
            logger.error("Structured response failed schema validation")
            raise AIResponseFormatError(
                "AI returned structured content that did not conform to the expected schema"
            ) from None

        u = response.usage
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=getattr(u, "completion_tokens", 0) if u else 0,
            total_tokens=getattr(u, "total_tokens", 0) if u else 0,
        )
        return StructuredResult(
            parsed=parsed,
            raw_content=raw_content,
            model=response.model or self.default_model,
            usage=usage,
        )

    def embed(
        self,
        context: TenantContext,
        texts: list[str],
        config: AIRequestConfig | None = None,
    ) -> EmbeddingResult:
        cfg = config or AIRequestConfig()
        kwargs: dict[str, Any] = {
            "model": self.default_embedding_model,
            "input": texts,
            "timeout": cfg.timeout_seconds,
        }

        response = self._execute_with_retry(
            "embed", cfg, lambda: self.client.embeddings.create(**kwargs)
        )

        embeddings = [item.embedding for item in response.data]
        u = response.usage
        usage = Usage(
            input_tokens=getattr(u, "prompt_tokens", 0) if u else 0,
            output_tokens=0,
            total_tokens=getattr(u, "total_tokens", 0) if u else 0,
        )
        return EmbeddingResult(
            embeddings=embeddings,
            model=response.model or self.default_embedding_model,
            usage=usage,
        )
