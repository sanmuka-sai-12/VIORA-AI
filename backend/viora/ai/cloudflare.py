"""Cloudflare Workers AI OpenAI-compatible configuration, capability registry, and gateway factory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .openai_adapter import OpenAIAdapter
from .schemas import (
    DEFAULT_TRUSTED_AI_HOSTS,
    AIConfigurationError,
    AIResourceLimits,
    validate_provider_endpoint,
)

_CF_ACCOUNT_RE = re.compile(r"^[a-zA-Z0-9_-]{8,64}$")
_CF_GATEWAY_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_CF_MODEL_RE = re.compile(r"^@(?:cf|hf)/[a-zA-Z0-9._/-]{3,128}$")

CLOUDFLARE_OPENAI_HOST = "api.cloudflare.com"
CLOUDFLARE_GATEWAY_HOST = "gateway.ai.cloudflare.com"
CLOUDFLARE_WORKER_HOST = "viora-ai.reddyanvesh2008.workers.dev"
DEFAULT_CLOUDFLARE_WORKER_URL = f"https://{CLOUDFLARE_WORKER_HOST}/v1"
DEFAULT_CLOUDFLARE_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast"


@dataclass(frozen=True)
class CloudflareModelCapabilities:
    """Verified Cloudflare Workers AI model metadata and Neuron pricing metrics."""

    model_id: str
    family: str
    context_window_tokens: int
    max_output_tokens: int
    supports_json_object: bool
    supports_json_schema: bool
    free_tier_eligible: bool
    latency_tier: Literal["ultra_low", "low", "balanced"]
    input_neurons_per_1m: int | None = None
    output_neurons_per_1m: int | None = None
    usd_per_1m_input_tokens: float | None = None
    usd_per_1m_output_tokens: float | None = None
    notes: str = ""


# Curated active instruction models verified against developers.cloudflare.com (Nov 2026).
# Note: @cf/meta/llama-3.2-3b-instruct and @cf/meta/llama-3.2-1b-instruct were retired Nov 20, 2026 (410 / 5028).
CLOUDFLARE_MODEL_SHORTLIST: dict[str, CloudflareModelCapabilities] = {
    "@cf/meta/llama-3.1-8b-instruct-fast": CloudflareModelCapabilities(
        model_id="@cf/meta/llama-3.1-8b-instruct-fast",
        family="llama-3.1",
        context_window_tokens=128_000,
        max_output_tokens=4_096,
        supports_json_object=True,
        supports_json_schema=True,
        free_tier_eligible=True,
        latency_tier="low",
        input_neurons_per_1m=4_119,
        output_neurons_per_1m=34_868,
        usd_per_1m_input_tokens=0.045,
        usd_per_1m_output_tokens=0.384,
        notes="Active accelerated 8B default; free-tier eligible (10k Neurons/day); supports json_schema.",
    ),
    "@cf/ibm-granite/granite-4.0-h-micro": CloudflareModelCapabilities(
        model_id="@cf/ibm-granite/granite-4.0-h-micro",
        family="granite-4.0",
        context_window_tokens=128_000,
        max_output_tokens=4_096,
        supports_json_object=True,
        supports_json_schema=True,
        free_tier_eligible=True,
        latency_tier="ultra_low",
        input_neurons_per_1m=1_542,
        output_neurons_per_1m=10_158,
        usd_per_1m_input_tokens=0.017,
        usd_per_1m_output_tokens=0.112,
        notes="Lowest Neuron cost in shortlist; free-tier eligible; built for structured RAG/JSON.",
    ),
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": CloudflareModelCapabilities(
        model_id="@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        family="llama-3.3",
        context_window_tokens=128_000,
        max_output_tokens=4_096,
        supports_json_object=True,
        supports_json_schema=True,
        free_tier_eligible=True,
        latency_tier="balanced",
        input_neurons_per_1m=26_668,
        output_neurons_per_1m=204_805,
        usd_per_1m_input_tokens=0.293,
        usd_per_1m_output_tokens=2.253,
        notes="Higher-accuracy FP8 70B fast model; higher Neuron draw (~6-7x 8B).",
    ),
}

FREE_TIER_DAILY_NEURONS = 10_000


def estimate_cloudflare_neurons(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, float | int | str | bool | None]:
    """Estimate Neuron usage against Cloudflare's 10,000 Neurons/day free tier."""
    caps = CLOUDFLARE_MODEL_SHORTLIST.get(model_id.strip())
    if caps is None or caps.input_neurons_per_1m is None or caps.output_neurons_per_1m is None:
        return {
            "model_id": model_id,
            "known_rate": False,
            "estimated_neurons": None,
            "daily_free_allocation": FREE_TIER_DAILY_NEURONS,
            "pct_of_daily_free_tier": None,
        }
    est = (
        (max(0, input_tokens) * caps.input_neurons_per_1m / 1_000_000.0)
        + (max(0, output_tokens) * caps.output_neurons_per_1m / 1_000_000.0)
    )
    return {
        "model_id": model_id,
        "known_rate": True,
        "estimated_neurons": round(est, 2),
        "daily_free_allocation": FREE_TIER_DAILY_NEURONS,
        "pct_of_daily_free_tier": round((est / FREE_TIER_DAILY_NEURONS) * 100.0, 3),
    }


class CloudflareProviderConfig(BaseModel):
    """Explicit server-side Cloudflare Workers AI OpenAI-compatible configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    account_id: str
    api_token: SecretStr
    model: str = DEFAULT_CLOUDFLARE_MODEL
    gateway_slug: str | None = None
    max_concurrency: int = Field(default=4, ge=1, le=16)

    def build_base_url(self) -> str:
        acct = self.account_id.strip()
        if not _CF_ACCOUNT_RE.fullmatch(acct):
            raise AIConfigurationError("Invalid Cloudflare account_id format")
        if self.gateway_slug is not None and self.gateway_slug.strip():
            slug = self.gateway_slug.strip()
            if not _CF_GATEWAY_RE.fullmatch(slug):
                raise AIConfigurationError("Invalid Cloudflare AI Gateway slug")
            return f"https://{CLOUDFLARE_GATEWAY_HOST}/v1/{acct}/{slug}/workers-ai/v1"
        return f"https://{CLOUDFLARE_OPENAI_HOST}/client/v4/accounts/{acct}/ai/v1"

    def validate_model(self) -> CloudflareModelCapabilities | None:
        cleaned = self.model.strip()
        if not _CF_MODEL_RE.fullmatch(cleaned):
            raise AIConfigurationError(
                "Cloudflare Workers AI model must begin with '@cf/' or '@hf/'"
            )
        lowered = cleaned.lower()
        if any(
            bad in lowered
            for bad in ("embed", "bge-", "flux", "stable-diffusion", "whisper", "melotts")
        ):
            raise AIConfigurationError(
                f"Cloudflare model '{cleaned}' is not a text-generation instruction model"
            )
        return CLOUDFLARE_MODEL_SHORTLIST.get(cleaned)


def build_cloudflare_gateway(
    config: CloudflareProviderConfig,
    *,
    client: object | None = None,
    resource_limits: AIResourceLimits | None = None,
    prefer_json_schema: bool = True,
) -> OpenAIAdapter:
    """Construct a hardened OpenAIAdapter targeting Cloudflare Workers AI."""
    raw_token = config.api_token.get_secret_value().strip()
    if not raw_token:
        raise AIConfigurationError("Cloudflare API token must not be empty")
    base_url = config.build_base_url()
    caps = config.validate_model()
    use_json_schema = bool(
        prefer_json_schema and (caps is None or caps.supports_json_schema)
    )
    limits = resource_limits or AIResourceLimits(
        max_messages=16,
        max_input_chars=48_000,
        max_output_tokens=min(2048, caps.max_output_tokens) if caps else 2048,
    )
    return OpenAIAdapter(
        api_key=config.api_token,
        base_url=base_url,
        default_model=config.model.strip(),
        client=client,  # type: ignore[arg-type]
        provider_name="cloudflare_workers_ai",
        trusted_hosts=(CLOUDFLARE_OPENAI_HOST, CLOUDFLARE_GATEWAY_HOST),
        allow_local_http=False,
        resource_limits=limits,
        max_concurrency=config.max_concurrency,
        structured_output_mode="json_schema" if use_json_schema else "json_object",
    )


class CloudflareWorkerConfig(BaseModel):
    """Explicit server-side Cloudflare Worker inference transport configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    worker_url: str = DEFAULT_CLOUDFLARE_WORKER_URL
    secret: SecretStr
    model: str = DEFAULT_CLOUDFLARE_MODEL
    trusted_hosts: tuple[str, ...] = tuple(sorted(DEFAULT_TRUSTED_AI_HOSTS))
    allow_local_http: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=8)

    def build_base_url(self) -> str:
        validated = validate_provider_endpoint(
            self.worker_url,
            trusted_hosts=frozenset(h.strip().lower() for h in self.trusted_hosts if h.strip()),
            allow_local_http=self.allow_local_http,
        )
        if not validated:
            raise AIConfigurationError("Cloudflare Worker URL is required")
        normalized = validated.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = f"{normalized}/v1"
        return normalized


def build_cloudflare_worker_gateway(
    config: CloudflareWorkerConfig,
    *,
    client: object | None = None,
    resource_limits: AIResourceLimits | None = None,
) -> OpenAIAdapter:
    """Construct an OpenAIAdapter targeting the thin viora-ai Cloudflare Worker."""
    raw_secret = config.secret.get_secret_value().strip()
    if not raw_secret:
        raise AIConfigurationError("CLOUDFLARE_WORKER_SECRET must not be empty")
    if config.model.strip() != DEFAULT_CLOUDFLARE_MODEL:
        raise AIConfigurationError(
            f"Worker transport only permits '{DEFAULT_CLOUDFLARE_MODEL}'"
        )
    base_url = config.build_base_url()
    limits = resource_limits or AIResourceLimits(
        max_messages=16,
        max_input_chars=12_000,
        max_output_tokens=500,
    )
    if limits.max_output_tokens > 500:
        limits = AIResourceLimits(
            max_messages=min(limits.max_messages, 16),
            max_input_chars=min(limits.max_input_chars, 12_000),
            max_output_tokens=500,
            max_embedding_inputs=limits.max_embedding_inputs,
            max_embedding_chars=limits.max_embedding_chars,
        )
    return OpenAIAdapter(
        api_key=config.secret,
        base_url=base_url,
        default_model=DEFAULT_CLOUDFLARE_MODEL,
        client=client,  # type: ignore[arg-type]
        provider_name="cloudflare_worker",
        trusted_hosts=config.trusted_hosts,
        allow_local_http=config.allow_local_http,
        resource_limits=limits,
        max_concurrency=config.max_concurrency,
        structured_output_mode="json_schema",
    )
