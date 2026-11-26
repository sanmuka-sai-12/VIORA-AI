import json
import logging
import random
from collections.abc import Callable, Iterable, Sequence
from threading import BoundedSemaphore
from time import monotonic, sleep
from typing import Any, Literal, Self, TypeVar
from urllib.parse import urlparse

import httpx
import openai
from openai import OpenAI
from pydantic import BaseModel, SecretStr, ValidationError

from ..contracts import TenantContext
from .ports import AIGateway
from .schemas import (
    DEFAULT_TRUSTED_AI_HOSTS,
    AIConfigurationError,
    AIContentFilterError,
    AIError,
    AIQuotaExhaustedError,
    AIRateLimitError,
    AIRequestConfig,
    AIResourceLimits,
    AIResponseFormatError,
    AITimeoutError,
    AITransientError,
    ChatMessage,
    CompletionResult,
    EmbeddingResult,
    GatewayMetadata,
    StructuredResult,
    Usage,
    validate_chat_budget,
    validate_embedding_budget,
    validate_provider_endpoint,
)

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


def _default_jitter(cap_seconds: float) -> float:
    return random.uniform(0.0, cap_seconds)


def _safe_nonneg_int(obj: Any, attr: str) -> int:
    val = getattr(obj, attr, None)
    if isinstance(val, int) and not isinstance(val, bool) and val >= 0:
        return val
    return 0


def _extract_usage(raw_usage: Any, *, include_output: bool = True) -> Usage:
    if raw_usage is None:
        return Usage()
    input_tokens = _safe_nonneg_int(raw_usage, "prompt_tokens")
    output_tokens = (
        _safe_nonneg_int(raw_usage, "completion_tokens") if include_output else 0
    )
    total_tokens = _safe_nonneg_int(raw_usage, "total_tokens")
    if total_tokens == 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def _extract_model_name(response: Any, fallback: str) -> str:
    raw = getattr(response, "model", None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else fallback


_QUOTA_EXHAUSTED_MARKERS = (
    "daily free allocation",
    "insufficient_quota",
    "ai_quota_exceeded",
    "ai_quota_exhausted",
    "neurons",
    "3036",
    "4006",
    "quota exceeded",
)


def _is_quota_exhausted_error(exc: Exception) -> bool:
    parts = [str(exc), str(getattr(exc, "code", "") or ""), str(getattr(exc, "body", "") or "")]
    combined = " ".join(parts).lower()
    return any(marker in combined for marker in _QUOTA_EXHAUSTED_MARKERS)


def _strip_json_code_fence(raw_content: str) -> str:
    """Unwrap an outer markdown ```json ... ``` fence without altering inner payloads."""
    trimmed = raw_content.strip()
    if trimmed.startswith("```"):
        lines = trimmed.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return trimmed


class OpenAIAdapter(AIGateway):
    """OpenAI-compatible AI gateway adapter (Cloudflare Workers AI / OpenAI / local).

    Disables SDK internal retries (max_retries=0) to avoid nested retry storms,
    reuses HTTP connections via httpx pooling, enforces concurrency bounds, adapter-level
    resource ceilings, trusted endpoints, and operation deadlines.

    Transport timeout note: ``min(timeout_seconds, remaining)`` bounds socket connect/read
    waits in httpx, while overall deadline checks strictly gate semaphore waits, retry
    backoff intervals, and attempts (synchronous sockets are not forcibly preempted
    mid-packet).
    """

    def __init__(
        self,
        api_key: SecretStr | None = None,
        base_url: str | None = None,
        default_model: str = "gpt-4o-mini",
        default_embedding_model: str = "text-embedding-3-small",
        client: OpenAI | None = None,
        *,
        provider_name: str = "openai_compatible",
        trusted_hosts: Iterable[str] | None = None,
        allow_local_http: bool = False,
        resource_limits: AIResourceLimits | None = None,
        max_concurrency: int = 8,
        max_connections: int = 16,
        max_keepalive_connections: int = 8,
        structured_output_mode: Literal["json_object", "json_schema"] = "json_object",
        clock: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = sleep,
        jitter_fn: Callable[[float], float] = _default_jitter,
    ) -> None:
        if max_concurrency < 1:
            raise AIConfigurationError("max_concurrency must be at least 1")
        effective_hosts = (
            frozenset(trusted_hosts)
            if trusted_hosts is not None
            else DEFAULT_TRUSTED_AI_HOSTS
        )
        validated_url = validate_provider_endpoint(
            base_url,
            trusted_hosts=effective_hosts,
            allow_local_http=allow_local_http,
        )
        self.default_model = default_model
        self.default_embedding_model = default_embedding_model
        self.provider_name = provider_name
        self.structured_output_mode = structured_output_mode
        self.hard_limits = resource_limits or AIResourceLimits()
        self._base_url = validated_url
        self._semaphore = BoundedSemaphore(max_concurrency)
        self._clock = clock
        self._sleep = sleep_fn
        self._jitter = jitter_fn
        self._owns_http_client = False
        self._http_client: httpx.Client | None = None

        if client is not None:
            self.client = client
        else:
            raw_key = api_key.get_secret_value().strip() if api_key else ""
            if not raw_key:
                raise AIConfigurationError(
                    "OpenAI API key must be provided when no custom client is supplied"
                )
            self._http_client = httpx.Client(
                limits=httpx.Limits(
                    max_connections=max_connections,
                    max_keepalive_connections=max_keepalive_connections,
                ),
            )
            self._owns_http_client = True
            self.client = OpenAI(
                api_key=raw_key,
                base_url=validated_url,
                max_retries=0,
                http_client=self._http_client,
            )

    def close(self) -> None:
        if self._owns_http_client and self._http_client is not None:
            self._http_client.close()
            self._http_client = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __repr__(self) -> str:
        safe_host = None
        if self._base_url:
            parsed = urlparse(self._base_url)
            safe_host = f"{parsed.scheme}://{parsed.hostname}"
        return f"<OpenAIAdapter provider={self.provider_name!r} model={self.default_model!r} endpoint={safe_host!r}>"

    def _log_event(
        self,
        level: int,
        *,
        event: str,
        context: TenantContext,
        operation: str,
        model: str,
        retry_count: int,
        latency_ms: float,
        input_chars: int = 0,
        output_chars: int = 0,
        usage: Usage | None = None,
        error_code: str | None = None,
    ) -> None:
        payload = {
            "event": event,
            "tenant_id": str(context.tenant_id),
            "provider": self.provider_name,
            "operation": operation,
            "model": model,
            "retry_count": retry_count,
            "latency_ms": round(latency_ms, 3),
            "input_chars": input_chars,
            "output_chars": output_chars,
        }
        if usage is not None:
            payload["input_tokens"] = usage.input_tokens
            payload["output_tokens"] = usage.output_tokens
            payload["total_tokens"] = usage.total_tokens
        if error_code is not None:
            payload["error_code"] = error_code
        logger.log(level, json.dumps(payload, sort_keys=True))

    def _execute_with_retry(
        self,
        context: TenantContext,
        operation_name: str,
        model: str,
        config: AIRequestConfig,
        call: Callable[[float], Any],
        *,
        input_chars: int,
    ) -> tuple[Any, int, float]:
        start = self._clock()
        deadline = start + config.total_deadline_seconds
        retries = 0
        max_retries = config.max_retries

        wait_budget = max(0.0, deadline - self._clock())
        acquired = self._semaphore.acquire(timeout=wait_budget)
        if not acquired:
            elapsed_ms = (self._clock() - start) * 1000.0
            self._log_event(
                logging.WARNING,
                event="ai_request_failed",
                context=context,
                operation=operation_name,
                model=model,
                retry_count=0,
                latency_ms=elapsed_ms,
                input_chars=input_chars,
                error_code="ai_timeout_error",
            )
            raise AITimeoutError("AI concurrency limit wait exceeded deadline") from None

        try:
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0.0:
                    raise AITimeoutError("AI operation deadline exceeded") from None
                request_timeout = min(config.timeout_seconds, remaining)

                try:
                    result = call(request_timeout)
                    elapsed_ms = (self._clock() - start) * 1000.0
                    return result, retries, elapsed_ms
                except openai.AuthenticationError:
                    elapsed_ms = (self._clock() - start) * 1000.0
                    self._log_event(
                        logging.ERROR,
                        event="ai_request_failed",
                        context=context,
                        operation=operation_name,
                        model=model,
                        retry_count=retries,
                        latency_ms=elapsed_ms,
                        input_chars=input_chars,
                        error_code="ai_configuration_error",
                    )
                    raise AIConfigurationError(
                        "Invalid or missing AI credentials"
                    ) from None
                except openai.RateLimitError as exc:
                    if _is_quota_exhausted_error(exc):
                        elapsed_ms = (self._clock() - start) * 1000.0
                        self._log_event(
                            logging.WARNING,
                            event="ai_request_failed",
                            context=context,
                            operation=operation_name,
                            model=model,
                            retry_count=retries,
                            latency_ms=elapsed_ms,
                            input_chars=input_chars,
                            error_code="ai_quota_exhausted",
                        )
                        raise AIQuotaExhaustedError(
                            "AI provider daily or account quota exhausted; stopping without retries"
                        ) from None
                    err_cls: type[AITransientError] = AIRateLimitError
                    err_msg = "AI provider rate limit exceeded"
                except (openai.APITimeoutError, TimeoutError):
                    err_cls = AITimeoutError
                    err_msg = "AI provider request timed out"
                except (openai.APIConnectionError, openai.InternalServerError):
                    err_cls = AITransientError
                    err_msg = "AI provider unavailable"
                except openai.BadRequestError as exc:
                    err_code = getattr(exc, "code", "")
                    elapsed_ms = (self._clock() - start) * 1000.0
                    if err_code == "content_filter":
                        self._log_event(
                            logging.WARNING,
                            event="ai_request_failed",
                            context=context,
                            operation=operation_name,
                            model=model,
                            retry_count=retries,
                            latency_ms=elapsed_ms,
                            input_chars=input_chars,
                            error_code="ai_content_filter_error",
                        )
                        raise AIContentFilterError(
                            "Content rejected by safety filters"
                        ) from None
                    self._log_event(
                        logging.ERROR,
                        event="ai_request_failed",
                        context=context,
                        operation=operation_name,
                        model=model,
                        retry_count=retries,
                        latency_ms=elapsed_ms,
                        input_chars=input_chars,
                        error_code="ai_bad_request",
                    )
                    raise AIError("ai_bad_request", "AI request rejected") from None
                except openai.OpenAIError:
                    elapsed_ms = (self._clock() - start) * 1000.0
                    self._log_event(
                        logging.ERROR,
                        event="ai_request_failed",
                        context=context,
                        operation=operation_name,
                        model=model,
                        retry_count=retries,
                        latency_ms=elapsed_ms,
                        input_chars=input_chars,
                        error_code="ai_provider_error",
                    )
                    raise AIError("ai_provider_error", "AI service failure") from None

                remaining_after = deadline - self._clock()
                if retries >= max_retries or remaining_after <= 0.0:
                    elapsed_ms = (self._clock() - start) * 1000.0
                    if remaining_after <= 0.0 and err_cls is not AITimeoutError and retries > 0:
                        final_exc: AITransientError = AITimeoutError(
                            "AI operation deadline exceeded"
                        )
                    else:
                        final_exc = err_cls(err_msg)
                    self._log_event(
                        logging.WARNING,
                        event="ai_request_failed",
                        context=context,
                        operation=operation_name,
                        model=model,
                        retry_count=retries,
                        latency_ms=elapsed_ms,
                        input_chars=input_chars,
                        error_code=final_exc.code,
                    )
                    raise final_exc from None

                cap = min(
                    config.max_backoff_seconds,
                    config.base_backoff_seconds * (2**retries),
                )
                delay = max(0.0, float(self._jitter(cap)))
                if delay >= remaining_after:
                    elapsed_ms = (self._clock() - start) * 1000.0
                    self._log_event(
                        logging.WARNING,
                        event="ai_request_failed",
                        context=context,
                        operation=operation_name,
                        model=model,
                        retry_count=retries,
                        latency_ms=elapsed_ms,
                        input_chars=input_chars,
                        error_code="ai_timeout_error",
                    )
                    raise AITimeoutError(
                        "AI operation deadline exceeded before retry"
                    ) from None

                retries += 1
                logger.warning(
                    json.dumps(
                        {
                            "event": "ai_request_retry",
                            "tenant_id": str(context.tenant_id),
                            "provider": self.provider_name,
                            "operation": operation_name,
                            "retry": retries,
                            "max_retries": max_retries,
                            "backoff_ms": round(delay * 1000.0, 2),
                        },
                        sort_keys=True,
                    )
                )
                if delay > 0.0:
                    self._sleep(delay)
        finally:
            self._semaphore.release()

    def _validate_completion_content(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices or not isinstance(choices, Sequence):
            raise AIResponseFormatError("AI provider returned no completion choices")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise AIResponseFormatError("AI provider choice omitted message payload")
        content = getattr(message, "content", None)
        if not isinstance(content, str):
            raise AIResponseFormatError("AI provider returned non-text completion content")
        return content

    def complete(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        config: AIRequestConfig | None = None,
    ) -> CompletionResult:
        cfg = config or AIRequestConfig()
        input_chars = validate_chat_budget(
            messages, cfg, hard_limits=self.hard_limits
        )
        formatted_messages = [
            {"role": m.role.value, "content": m.content} for m in messages
        ]

        base_kwargs: dict[str, Any] = {
            "model": self.default_model,
            "messages": formatted_messages,
        }
        if cfg.max_tokens is not None:
            base_kwargs["max_tokens"] = cfg.max_tokens
        if cfg.temperature is not None:
            base_kwargs["temperature"] = cfg.temperature

        response, retries, latency_ms = self._execute_with_retry(
            context,
            "complete",
            self.default_model,
            cfg,
            lambda req_timeout: self.client.chat.completions.create(
                **base_kwargs, timeout=req_timeout
            ),
            input_chars=input_chars,
        )

        content = self._validate_completion_content(response)
        usage = _extract_usage(getattr(response, "usage", None), include_output=True)
        model_used = _extract_model_name(response, self.default_model)
        metadata = GatewayMetadata(
            provider=self.provider_name,
            latency_ms=latency_ms,
            retry_count=retries,
            attempts=retries + 1,
            simulated=False,
            input_chars=input_chars,
            output_chars=len(content),
        )
        self._log_event(
            logging.INFO,
            event="ai_request_succeeded",
            context=context,
            operation="complete",
            model=model_used,
            retry_count=retries,
            latency_ms=latency_ms,
            input_chars=input_chars,
            output_chars=len(content),
            usage=usage,
        )
        return CompletionResult(
            content=content,
            model=model_used,
            usage=usage,
            metadata=metadata,
        )

    def complete_structured(
        self,
        context: TenantContext,
        messages: list[ChatMessage],
        response_model: type[T],
        config: AIRequestConfig | None = None,
    ) -> StructuredResult[T]:
        cfg = config or AIRequestConfig()
        input_chars = validate_chat_budget(
            messages, cfg, hard_limits=self.hard_limits
        )
        formatted_messages = [
            {"role": m.role.value, "content": m.content} for m in messages
        ]

        schema_json = response_model.model_json_schema()
        schema_prompt = (
            "\nStrict response requirement: respond in valid JSON conforming to the following schema:\n"
            f"{json.dumps(schema_json)}"
        )
        if formatted_messages and formatted_messages[0]["role"] == "system":
            formatted_messages[0]["content"] += schema_prompt
        else:
            formatted_messages.insert(
                0, {"role": "system", "content": schema_prompt.strip()}
            )

        if self.structured_output_mode == "json_schema":
            response_format: dict[str, Any] = {
                "type": "json_schema",
                "json_schema": schema_json,
            }
        else:
            response_format = {"type": "json_object"}

        base_kwargs: dict[str, Any] = {
            "model": self.default_model,
            "messages": formatted_messages,
            "response_format": response_format,
        }
        if cfg.max_tokens is not None:
            base_kwargs["max_tokens"] = cfg.max_tokens
        if cfg.temperature is not None:
            base_kwargs["temperature"] = cfg.temperature

        response, retries, latency_ms = self._execute_with_retry(
            context,
            "complete_structured",
            self.default_model,
            cfg,
            lambda req_timeout: self.client.chat.completions.create(
                **base_kwargs, timeout=req_timeout
            ),
            input_chars=input_chars,
        )

        raw_content = self._validate_completion_content(response)
        cleaned_json = _strip_json_code_fence(raw_content)
        try:
            parsed = response_model.model_validate_json(cleaned_json)
        except (ValidationError, json.JSONDecodeError):
            self._log_event(
                logging.ERROR,
                event="ai_request_failed",
                context=context,
                operation="complete_structured",
                model=self.default_model,
                retry_count=retries,
                latency_ms=latency_ms,
                input_chars=input_chars,
                error_code="ai_response_format_error",
            )
            raise AIResponseFormatError(
                "AI returned structured content that did not conform to the expected schema"
            ) from None

        usage = _extract_usage(getattr(response, "usage", None), include_output=True)
        model_used = _extract_model_name(response, self.default_model)
        metadata = GatewayMetadata(
            provider=self.provider_name,
            latency_ms=latency_ms,
            retry_count=retries,
            attempts=retries + 1,
            simulated=False,
            input_chars=input_chars,
            output_chars=len(raw_content),
        )
        self._log_event(
            logging.INFO,
            event="ai_request_succeeded",
            context=context,
            operation="complete_structured",
            model=model_used,
            retry_count=retries,
            latency_ms=latency_ms,
            input_chars=input_chars,
            output_chars=len(raw_content),
            usage=usage,
        )
        return StructuredResult(
            parsed=parsed,
            raw_content=raw_content,
            model=model_used,
            usage=usage,
            metadata=metadata,
        )

    def embed(
        self,
        context: TenantContext,
        texts: list[str],
        config: AIRequestConfig | None = None,
    ) -> EmbeddingResult:
        cfg = config or AIRequestConfig()
        input_chars = validate_embedding_budget(
            texts, cfg, hard_limits=self.hard_limits
        )
        base_kwargs: dict[str, Any] = {
            "model": self.default_embedding_model,
            "input": texts,
        }

        response, retries, latency_ms = self._execute_with_retry(
            context,
            "embed",
            self.default_embedding_model,
            cfg,
            lambda req_timeout: self.client.embeddings.create(
                **base_kwargs, timeout=req_timeout
            ),
            input_chars=input_chars,
        )

        data = getattr(response, "data", None)
        if not data or not isinstance(data, Sequence) or len(data) != len(texts):
            raise AIResponseFormatError(
                "Malformed embedding response count from AI provider"
            ) from None

        embeddings: list[list[float]] = []
        expected_dim: int | None = None
        for item in data:
            vec = getattr(item, "embedding", None)
            if (
                not isinstance(vec, Sequence)
                or isinstance(vec, (str, bytes))
                or len(vec) == 0
            ):
                raise AIResponseFormatError(
                    "Malformed embedding vector from AI provider"
                ) from None
            if expected_dim is None:
                expected_dim = len(vec)
            elif len(vec) != expected_dim:
                raise AIResponseFormatError(
                    "Inconsistent embedding vector dimensions from AI provider"
                ) from None
            try:
                embeddings.append([float(x) for x in vec])
            except (TypeError, ValueError):
                raise AIResponseFormatError(
                    "Non-numeric embedding component from AI provider"
                ) from None

        usage = _extract_usage(getattr(response, "usage", None), include_output=False)
        model_used = _extract_model_name(response, self.default_embedding_model)
        metadata = GatewayMetadata(
            provider=self.provider_name,
            latency_ms=latency_ms,
            retry_count=retries,
            attempts=retries + 1,
            simulated=False,
            input_chars=input_chars,
            output_chars=0,
        )
        self._log_event(
            logging.INFO,
            event="ai_request_succeeded",
            context=context,
            operation="embed",
            model=model_used,
            retry_count=retries,
            latency_ms=latency_ms,
            input_chars=input_chars,
            usage=usage,
        )
        return EmbeddingResult(
            embeddings=embeddings,
            model=model_used,
            usage=usage,
            metadata=metadata,
        )
