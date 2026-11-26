from unittest.mock import MagicMock
from uuid import uuid4

import openai
import pytest
from pydantic import BaseModel, SecretStr
from viora.ai.openai_adapter import OpenAIAdapter
from viora.ai.public import (
    AIConfigurationError,
    AIContentFilterError,
    AIRateLimitError,
    AIRequestConfig,
    AIResponseFormatError,
    AITimeoutError,
    ChatMessage,
    FakeAIGateway,
    Role,
)
from viora.contracts import TenantContext
from viora.ports import AIProviderGatewayAdapter


class MockOutlineSchema(BaseModel):
    title: str
    key_points: list[str]


@pytest.fixture
def dummy_context() -> TenantContext:
    return TenantContext(
        actor_id=uuid4(),
        tenant_id=uuid4(),
        roles=frozenset({"teacher"}),
    )


def test_fake_ai_gateway_completion_and_embedding(dummy_context: TenantContext) -> None:
    fake = FakeAIGateway(default_completion="Test lesson outline")
    messages = [ChatMessage(role=Role.user, content="Explain Newton's third law")]
    res = fake.complete(dummy_context, messages)

    assert res.content == "Test lesson outline"
    assert res.usage.total_tokens > 0
    assert len(fake.recorded_calls) == 1

    embed_res = fake.embed(dummy_context, ["First text", "Second text"])
    assert len(embed_res.embeddings) == 2
    assert len(embed_res.embeddings[0]) == 1536
    assert embed_res.usage.total_tokens > 0


def test_fake_ai_gateway_structured_completion(dummy_context: TenantContext) -> None:
    expected = MockOutlineSchema(title="Motion", key_points=["Velocity", "Acceleration"])
    fake = FakeAIGateway(structured_payloads={MockOutlineSchema: expected})
    messages = [ChatMessage(role=Role.user, content="Generate motion outline")]

    res = fake.complete_structured(dummy_context, messages, MockOutlineSchema)
    assert res.parsed == expected
    assert "Motion" in res.raw_content


def test_openai_adapter_successful_completion(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Calculus explanation"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = "gpt-4o-mini"
    mock_response.usage.prompt_tokens = 15
    mock_response.usage.completion_tokens = 25
    mock_response.usage.total_tokens = 40
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    res = adapter.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content="Teach limits")],
        AIRequestConfig(max_tokens=100, temperature=0.2),
    )

    assert res.content == "Calculus explanation"
    assert res.usage.total_tokens == 40
    mock_client.chat.completions.create.assert_called_once()
    _, kwargs = mock_client.chat.completions.create.call_args
    assert kwargs["max_tokens"] == 100
    assert kwargs["temperature"] == 0.2


def test_openai_adapter_structured_validation_failure(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "not a valid json object"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = None
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AIResponseFormatError, match="did not conform"):
        adapter.complete_structured(
            dummy_context,
            [ChatMessage(role=Role.user, content="Give me json")],
            MockOutlineSchema,
        )


def test_openai_adapter_retry_on_transient_error(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Finally succeeded"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage = None

    # Fail once with connection error, then succeed
    mock_client.chat.completions.create.side_effect = [
        openai.APIConnectionError(request=MagicMock()),
        mock_response,
    ]

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    res = adapter.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content="Retry test")],
        AIRequestConfig(max_retries=1),
    )
    assert res.content == "Finally succeeded"
    assert mock_client.chat.completions.create.call_count == 2


def test_openai_adapter_bounded_retry_limit_exceeded(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        openai.RateLimitError(message="rate limit", response=MagicMock(), body=None),
        openai.RateLimitError(message="rate limit", response=MagicMock(), body=None),
        openai.RateLimitError(message="rate limit", response=MagicMock(), body=None),
    ]

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AIRateLimitError):
        adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content="Rate limit test")],
            AIRequestConfig(max_retries=2),
        )
    # 1 initial attempt + 2 retries = 3 calls
    assert mock_client.chat.completions.create.call_count == 3


def test_openai_adapter_timeout_handling(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = [
        openai.APITimeoutError(request=MagicMock())
    ]

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AITimeoutError):
        adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content="Timeout test")],
            AIRequestConfig(max_retries=0),
        )


def test_openai_adapter_authentication_error(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = openai.AuthenticationError(
        message="invalid key", response=MagicMock(), body=None
    )

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AIConfigurationError, match="Invalid or missing AI credentials"):
        adapter.complete(dummy_context, [ChatMessage(role=Role.user, content="Test")])


def test_openai_adapter_content_filter_error(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    bad_req = openai.BadRequestError(
        message="Safety triggered", response=MagicMock(), body=None
    )
    bad_req.code = "content_filter"
    mock_client.chat.completions.create.side_effect = bad_req

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AIContentFilterError, match="Content rejected by safety filters"):
        adapter.complete(dummy_context, [ChatMessage(role=Role.user, content="Test")])


def test_openai_adapter_embeddings(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_item = MagicMock()
    mock_item.embedding = [0.1, 0.2, 0.3]
    mock_response = MagicMock()
    mock_response.data = [mock_item]
    mock_response.model = "text-embedding-3-small"
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.total_tokens = 5
    mock_client.embeddings.create.return_value = mock_response

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    res = adapter.embed(dummy_context, ["Embedding sample"])

    assert len(res.embeddings) == 1
    assert res.embeddings[0] == [0.1, 0.2, 0.3]
    assert res.usage.input_tokens == 5


def test_ai_provider_gateway_adapter(dummy_context: TenantContext) -> None:
    fake = FakeAIGateway(default_completion="Adapted completion")
    adapter = AIProviderGatewayAdapter(fake)

    result = adapter.complete(dummy_context, "Simple prompt")
    assert result == "Adapted completion"
    assert len(fake.recorded_calls) == 1
    assert fake.recorded_calls[0]["messages"][0].content == "Simple prompt"


def test_openai_adapter_endpoint_security_validation() -> None:
    # Remote insecure HTTP is strictly forbidden
    with pytest.raises(AIConfigurationError, match="Insecure HTTP AI endpoint"):
        OpenAIAdapter(
            api_key=SecretStr("mock-key"),
            base_url="http://insecure-remote-api.com/v1",
            client=MagicMock(),
        )

    # Invalid scheme is rejected
    with pytest.raises(AIConfigurationError, match="Unsupported AI endpoint scheme"):
        OpenAIAdapter(
            api_key=SecretStr("mock-key"),
            base_url="ftp://api.example.com",
            client=MagicMock(),
        )

    # Localhost HTTP requires explicit opt-in and is forbidden by default
    with pytest.raises(AIConfigurationError, match="explicit development configuration"):
        OpenAIAdapter(
            api_key=SecretStr("mock-key"),
            base_url="http://localhost:11434/v1",
            client=MagicMock(),
        )

    local_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="http://localhost:11434/v1",
        allow_local_http=True,
        client=MagicMock(),
    )
    assert local_adapter._base_url == "http://localhost:11434/v1"

    # Arbitrary untrusted HTTPS hosts are rejected unless explicitly listed
    with pytest.raises(AIConfigurationError, match="Untrusted AI endpoint host"):
        OpenAIAdapter(
            api_key=SecretStr("mock-key"),
            base_url="https://arbitrary-external.example/v1",
            client=MagicMock(),
        )

    # Trusted defaults and explicitly trusted institutional hosts are permitted
    secure_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="https://api.openai.com/v1",
        client=MagicMock(),
    )
    assert secure_adapter._base_url == "https://api.openai.com/v1"

    custom_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="https://ai.campus.example/v1",
        trusted_hosts={"ai.campus.example"},
        client=MagicMock(),
    )
    assert custom_adapter._base_url == "https://ai.campus.example/v1"


def test_openai_adapter_missing_api_key_without_client() -> None:
    with pytest.raises(AIConfigurationError, match="OpenAI API key must be provided"):
        OpenAIAdapter(api_key=None, client=None)

    with pytest.raises(AIConfigurationError, match="OpenAI API key must be provided"):
        OpenAIAdapter(api_key=SecretStr("   "), client=None)


def test_openai_adapter_request_token_limit_enforcement(dummy_context: TenantContext) -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Limited output"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = "gpt-4o-mini"
    mock_response.usage.prompt_tokens = 5
    mock_response.usage.completion_tokens = 2
    mock_response.usage.total_tokens = 7
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    res = adapter.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content="Prompt")],
        AIRequestConfig(max_tokens=64, temperature=0.7),
    )

    assert res.content == "Limited output"
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["max_tokens"] == 64
    assert call_kwargs["temperature"] == 0.7


def test_fake_ai_gateway_token_limit_enforcement(dummy_context: TenantContext) -> None:
    fake = FakeAIGateway(default_completion="A" * 100)
    # max_tokens=10 -> approx 40 chars max
    res = fake.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content="Prompt")],
        AIRequestConfig(max_tokens=10),
    )
    assert len(res.content) == 40


def test_structured_response_sanitizes_underlying_validation_cause(
    dummy_context: TenantContext,
) -> None:
    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = '{"unrecognized_key": "confidential student data"}'
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_client.chat.completions.create.return_value = mock_response

    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    with pytest.raises(AIResponseFormatError) as exc_info:
        adapter.complete_structured(
            dummy_context,
            [ChatMessage(role=Role.user, content="Extract")],
            MockOutlineSchema,
        )

    # __cause__ must be None so tracebacks cannot leak payload data
    assert exc_info.value.__cause__ is None


def test_openai_adapter_backoff_jitter_and_operation_deadline(
    dummy_context: TenantContext,
) -> None:
    slept: list[float] = []
    fake_now = [100.0]

    def fake_clock() -> float:
        return fake_now[0]

    def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        fake_now[0] += seconds

    mock_client = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = "Recovered after jittered waits"
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = "gpt-4o-mini"
    mock_response.usage.prompt_tokens = 10
    mock_response.usage.completion_tokens = 5
    mock_response.usage.total_tokens = 15

    mock_client.chat.completions.create.side_effect = [
        openai.InternalServerError(message="503", response=MagicMock(), body=None),
        openai.APIConnectionError(request=MagicMock()),
        mock_response,
    ]

    adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        client=mock_client,
        clock=fake_clock,
        sleep_fn=fake_sleep,
        jitter_fn=lambda cap: cap * 0.5,
    )
    res = adapter.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content="Recover")],
        AIRequestConfig(
            max_retries=3,
            base_backoff_seconds=0.2,
            max_backoff_seconds=0.35,
            total_deadline_seconds=10.0,
        ),
    )

    assert res.content == "Recovered after jittered waits"
    assert res.metadata.retry_count == 2
    assert res.metadata.attempts == 3
    assert res.metadata.simulated is False
    # Retry 0 cap = 0.2 -> half = 0.1; Retry 1 cap = min(0.35, 0.4) -> half = 0.175
    assert slept == [pytest.approx(0.1), pytest.approx(0.175)]

    # Tight total deadline rejects retry wait cleanly instead of sleeping past deadline
    fake_now[0] = 200.0
    slept.clear()
    mock_client.chat.completions.create.side_effect = [
        openai.RateLimitError(message="429", response=MagicMock(), body=None),
        openai.RateLimitError(message="429", response=MagicMock(), body=None),
    ]
    tight_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        client=mock_client,
        clock=fake_clock,
        sleep_fn=fake_sleep,
        jitter_fn=lambda cap: cap,
    )
    with pytest.raises(AITimeoutError, match="deadline"):
        tight_adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content="Tight deadline")],
            AIRequestConfig(
                max_retries=2,
                base_backoff_seconds=1.5,
                total_deadline_seconds=1.0,
            ),
        )
    assert slept == []


def test_openai_adapter_endpoint_credential_and_query_rejection() -> None:
    for bad_url in (
        "https://user:secret@api.openai.com/v1",
        "https://api.openai.com/v1?token=abc",
        "https://api.openai.com/v1#frag",
        "https:///v1",
        "   ",
    ):
        with pytest.raises(AIConfigurationError):
            OpenAIAdapter(
                api_key=SecretStr("mock-key"),
                base_url=bad_url,
                client=MagicMock(),
            )


def test_ai_gateway_resource_limits_enforced(dummy_context: TenantContext) -> None:
    from viora.ai.public import AIResourceLimitError, AIResourceLimits

    limits = AIResourceLimits(
        max_messages=2,
        max_input_chars=25,
        max_output_tokens=50,
        max_embedding_texts=2,
        max_embedding_chars_per_text=10,
        max_embedding_total_chars=15,
    )
    cfg = AIRequestConfig(resource_limits=limits)
    mock_client = MagicMock()
    adapter = OpenAIAdapter(api_key=SecretStr("mock-key"), client=mock_client)
    fake = FakeAIGateway()

    for gw in (adapter, fake):
        with pytest.raises(AIResourceLimitError):
            gw.complete(dummy_context, [], cfg)
        with pytest.raises(AIResourceLimitError):
            gw.complete(
                dummy_context,
                [ChatMessage(role=Role.user, content="a")] * 3,
                cfg,
            )
        with pytest.raises(AIResourceLimitError):
            gw.complete(
                dummy_context,
                [ChatMessage(role=Role.user, content="x" * 26)],
                cfg,
            )
        with pytest.raises(AIResourceLimitError):
            gw.complete(
                dummy_context,
                [ChatMessage(role=Role.user, content="ok")],
                AIRequestConfig(max_tokens=51, resource_limits=limits),
            )
        with pytest.raises(AIResourceLimitError):
            gw.embed(dummy_context, ["a", "b", "c"], cfg)
        with pytest.raises(AIResourceLimitError):
            gw.embed(dummy_context, ["01234567890"], cfg)
        with pytest.raises(AIResourceLimitError):
            gw.embed(dummy_context, ["01234567", "01234567"], cfg)

    mock_client.chat.completions.create.assert_not_called()
    mock_client.embeddings.create.assert_not_called()

    # Caller cannot override adapter-configured hard resource upper bounds upward
    strict_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        client=mock_client,
        resource_limits=AIResourceLimits(max_input_chars=20, max_output_tokens=16),
    )
    caller_override = AIRequestConfig(
        max_tokens=32,
        resource_limits=AIResourceLimits(max_input_chars=500_000, max_output_tokens=32_768),
    )
    with pytest.raises(AIResourceLimitError):
        strict_adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content="x" * 21)],
            caller_override,
        )
    with pytest.raises(AIResourceLimitError):
        strict_adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content="ok")],
            caller_override,
        )

    from viora.config import Settings

    with pytest.raises(ValueError, match="Local insecure AI endpoints"):
        Settings.model_validate(
            {
                "database_url": "postgresql+psycopg://localhost/viora_test",
                "environment": "production",
                "allow_local_ai_endpoint": True,
            }
        )


def test_ai_gateway_malformed_responses_and_sensitive_log_redaction(
    dummy_context: TenantContext, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="viora.ai.openai_adapter")

    mock_client = MagicMock()
    secret_prompt = "CONFIDENTIAL_STUDENT_ALICE_PROMPT"
    secret_output = "SENSITIVE_PRIVATE_OUTPUT"

    bad_choices = MagicMock()
    bad_choices.choices = []
    mock_client.chat.completions.create.return_value = bad_choices

    adapter = OpenAIAdapter(
        api_key=SecretStr("sk-super-secret-token-value"),
        base_url="https://api.cloudflare.com/client/v4/accounts/acct123/ai/v1",
        client=mock_client,
    )
    assert "sk-super-secret" not in repr(adapter)

    with pytest.raises(AIResponseFormatError):
        adapter.complete(
            dummy_context,
            [ChatMessage(role=Role.user, content=secret_prompt)],
        )

    good_choice = MagicMock()
    good_choice.message.content = secret_output
    good_resp = MagicMock()
    good_resp.choices = [good_choice]
    good_resp.model = "workers-ai"
    good_resp.usage.prompt_tokens = 12
    good_resp.usage.completion_tokens = 8
    good_resp.usage.total_tokens = 0  # Test auto-sum when total omitted
    mock_client.chat.completions.create.return_value = good_resp

    ok = adapter.complete(
        dummy_context,
        [ChatMessage(role=Role.user, content=secret_prompt)],
    )
    assert ok.usage.total_tokens == 20

    # Malformed embedding count rejected
    bad_embed = MagicMock()
    bad_embed.data = []
    mock_client.embeddings.create.return_value = bad_embed
    with pytest.raises(AIResponseFormatError):
        adapter.embed(dummy_context, ["one"])

    combined_logs = "\n".join(rec.getMessage() for rec in caplog.records)
    assert secret_prompt not in combined_logs
    assert secret_output not in combined_logs
    assert "sk-super-secret" not in combined_logs
    assert "ai_request_succeeded" in combined_logs


def test_openai_adapter_connection_pool_and_bounded_concurrency(
    dummy_context: TenantContext,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock
    from time import sleep

    # Real OpenAIAdapter instance without custom client builds reusable http_client cleanly
    with OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="http://127.0.0.1:11434/v1",
        allow_local_http=True,
        max_concurrency=2,
    ) as pooled:
        assert pooled._http_client is not None
    assert pooled._http_client is None

    active = 0
    max_seen = 0
    lock = Lock()

    def slow_create(**_kwargs: object) -> MagicMock:
        nonlocal active, max_seen
        with lock:
            active += 1
            max_seen = max(max_seen, active)
        sleep(0.03)
        with lock:
            active -= 1
        ch = MagicMock()
        ch.message.content = "ok"
        r = MagicMock()
        r.choices = [ch]
        r.model = "m"
        r.usage = None
        return r

    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = slow_create
    bounded = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        client=mock_client,
        max_concurrency=2,
    )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(
                lambda _: bounded.complete(
                    dummy_context, [ChatMessage(role=Role.user, content="hi")]
                ),
                range(6),
            )
        )
    assert len(results) == 6
    assert max_seen <= 2


