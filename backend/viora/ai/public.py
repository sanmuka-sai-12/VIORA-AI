from ..config import Settings
from .cloudflare import (
    CLOUDFLARE_GATEWAY_HOST,
    CLOUDFLARE_MODEL_SHORTLIST,
    CLOUDFLARE_OPENAI_HOST,
    CLOUDFLARE_WORKER_HOST,
    DEFAULT_CLOUDFLARE_MODEL,
    DEFAULT_CLOUDFLARE_WORKER_URL,
    FREE_TIER_DAILY_NEURONS,
    CloudflareModelCapabilities,
    CloudflareProviderConfig,
    CloudflareWorkerConfig,
    build_cloudflare_gateway,
    build_cloudflare_worker_gateway,
    estimate_cloudflare_neurons,
)
from .fake import FakeAIGateway
from .openai_adapter import OpenAIAdapter
from .ports import AIGateway
from .schemas import (
    DEFAULT_TRUSTED_AI_HOSTS,
    AIConfigurationError,
    AIContentFilterError,
    AIError,
    AIQuotaExhaustedError,
    AIRateLimitError,
    AIRequestConfig,
    AIResourceLimitError,
    AIResourceLimits,
    AIResponseFormatError,
    AITimeoutError,
    AITransientError,
    ChatMessage,
    CompletionResult,
    EmbeddingResult,
    GatewayMetadata,
    Role,
    StructuredResult,
    Usage,
    clamp_resource_limits,
    validate_provider_endpoint,
)

__all__ = [
    "CLOUDFLARE_GATEWAY_HOST",
    "CLOUDFLARE_MODEL_SHORTLIST",
    "CLOUDFLARE_OPENAI_HOST",
    "CLOUDFLARE_WORKER_HOST",
    "DEFAULT_CLOUDFLARE_MODEL",
    "DEFAULT_CLOUDFLARE_WORKER_URL",
    "DEFAULT_TRUSTED_AI_HOSTS",
    "FREE_TIER_DAILY_NEURONS",
    "AIConfigurationError",
    "AIContentFilterError",
    "AIError",
    "AIGateway",
    "AIQuotaExhaustedError",
    "AIRateLimitError",
    "AIRequestConfig",
    "AIResourceLimitError",
    "AIResourceLimits",
    "AIResponseFormatError",
    "AITimeoutError",
    "AITransientError",
    "ChatMessage",
    "CloudflareModelCapabilities",
    "CloudflareProviderConfig",
    "CloudflareWorkerConfig",
    "CompletionResult",
    "EmbeddingResult",
    "FakeAIGateway",
    "GatewayMetadata",
    "OpenAIAdapter",
    "Role",
    "StructuredResult",
    "Usage",
    "build_ai_gateway_from_settings",
    "build_cloudflare_gateway",
    "build_cloudflare_worker_gateway",
    "clamp_resource_limits",
    "estimate_cloudflare_neurons",
    "validate_provider_endpoint",
]


def build_ai_gateway_from_settings(settings: Settings) -> AIGateway | None:
    """Explicitly construct a server-side AIGateway when configured; returns None when disabled."""
    if settings.ai_provider == "none":
        return None
    if settings.ai_provider == "cloudflare":
        if not settings.cloudflare_account_id or settings.cloudflare_api_token is None:
            raise AIConfigurationError(
                "AI_PROVIDER=cloudflare requires CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN"
            )
        return build_cloudflare_gateway(
            CloudflareProviderConfig(
                account_id=settings.cloudflare_account_id,
                api_token=settings.cloudflare_api_token,
                model=settings.cloudflare_ai_model,
                gateway_slug=settings.cloudflare_ai_gateway_slug,
            )
        )
    if settings.ai_provider == "cloudflare_worker":
        if settings.cloudflare_worker_secret is None:
            raise AIConfigurationError(
                "AI_PROVIDER=cloudflare_worker requires CLOUDFLARE_WORKER_SECRET"
            )
        return build_cloudflare_worker_gateway(
            CloudflareWorkerConfig(
                worker_url=settings.cloudflare_worker_url or DEFAULT_CLOUDFLARE_WORKER_URL,
                secret=settings.cloudflare_worker_secret,
                model=settings.cloudflare_ai_model,
                trusted_hosts=settings.trusted_ai_hosts,
                allow_local_http=settings.allow_local_ai_endpoint,
            )
        )
    if settings.ai_provider == "openai":
        if settings.openai_api_key is None:
            raise AIConfigurationError("AI_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIAdapter(
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            default_model=settings.default_ai_model,
            default_embedding_model=settings.default_embedding_model,
            trusted_hosts=settings.trusted_ai_hosts,
            allow_local_http=settings.allow_local_ai_endpoint,
        )
    raise AIConfigurationError(f"Unsupported AI provider: {settings.ai_provider}")
