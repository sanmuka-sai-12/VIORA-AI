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

    # Localhost HTTP is permitted for local development/mocks
    local_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="http://localhost:11434/v1",
        client=MagicMock(),
    )
    assert local_adapter._base_url == "http://localhost:11434/v1"

    # HTTPS is permitted
    secure_adapter = OpenAIAdapter(
        api_key=SecretStr("mock-key"),
        base_url="https://api.openai.com/v1",
        client=MagicMock(),
    )
    assert secure_adapter._base_url == "https://api.openai.com/v1"


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


