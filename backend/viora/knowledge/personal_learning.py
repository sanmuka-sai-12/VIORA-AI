"""Bounded direct academic learning service powered by AIGateway."""

from __future__ import annotations

import re

from ..ai.public import AIGateway, AIRequestConfig, ChatMessage, CompletionResult, Role
from ..contracts import TenantContext
from .prompts import sanitize_untrusted_text
from .schemas import PersonalLearningRequest, PersonalLearningResponse

_MAX_HISTORY_CHARS = 7_500
_MAX_TOTAL_TURNS = 10
_MAX_OUTPUT_TOKENS = 480

_LEARNING_SYSTEM_PROMPT = (
    "You are VIORA, an academic learning assistant for students.\n"
    "Answer clearly, accurately, and concisely so your entire response fits cleanly within ~150-220 words.\n"
    "ACCURACY & CONSISTENCY:\n"
    "- Keep all worked examples, calculations, and algorithm traces 100% consistent from start to finish.\n"
    "- For array/sequence algorithms, state the indexing convention explicitly (use 0-based indexing consistently unless asked otherwise) and keep exact array values and indices aligned at every step.\n"
    "FORMATTING:\n"
    "- Use clean markdown headings with a space (e.g. `## Concept`, `## Example`).\n"
    "- Use LaTeX math: `$...$` for inline math and `$$...$$` on its own lines for display equations.\n"
    "- Use fenced code blocks (e.g. ```python) with at most ~10 lines of code.\n"
    "- Always finish with a complete final sentence. If a topic is broad, cover the core cleanly and let the student ask follow-ups."
)

_TRAILING_INCOMPLETE_WORD_RE = re.compile(
    r"(?:^|\s)(?:and|or|but|the|a|an|to|of|in|on|at|for|with|by|from|as|is|are|was|were|be|that|which|where|when|while|if|then|than|using|into|because)$",
    re.IGNORECASE,
)


def is_response_truncated(
    result: CompletionResult,
    *,
    max_tokens: int = _MAX_OUTPUT_TOKENS,
) -> bool:
    """Detect whether an AI completion hit token limits or ended mid-thought."""
    if result.finish_reason == "length":
        return True

    text = result.content.strip()
    if not text:
        return True

    near_limit = result.usage.output_tokens >= max(1, max_tokens - 16)

    fence_lines = sum(
        1 for line in text.splitlines() if line.lstrip().startswith("```")
    )
    if fence_lines % 2 == 1:
        return True

    without_fences = re.sub(r"```[\s\S]*?```", "", text)
    if without_fences.count("$$") % 2 == 1:
        return True
    if without_fences.count("\\[") > without_fences.count("\\]"):
        return True

    if text[-1] in {",", ";", "(", "[", "{", "+", "=", "\\"}:
        return True
    if text.endswith("-") and not text.endswith("---"):
        return True
    if _TRAILING_INCOMPLETE_WORD_RE.search(text):
        return True

    if near_limit and text[-1] not in {".", "!", "?", "`", "$", ")", '"', "'"}:
        return True

    last_line = text.splitlines()[-1].strip()
    is_heading = last_line.startswith("#")
    is_list_item = bool(re.match(r"^(?:[-*]|\d+\.)\s+", last_line))
    return (
        not is_heading
        and not is_list_item
        and len(text) >= 80
        and text[-1].islower()
    )


def build_personal_learning_messages(
    req: PersonalLearningRequest,
) -> list[ChatMessage]:
    """Construct bounded chat messages from sanitized prompt and recent history."""
    clean_prompt, _ = sanitize_untrusted_text(req.prompt.strip())
    mode_prefix = (
        "Explain this academic topic concisely with core intuition, key rule/equation, and one short consistent example:\n"
        if req.mode == "explain"
        else ""
    )
    user_message = f"{mode_prefix}{clean_prompt}".strip()

    kept_history: list[ChatMessage] = []
    used_chars = len(_LEARNING_SYSTEM_PROMPT) + len(user_message)
    for turn in reversed(req.history[-_MAX_TOTAL_TURNS:]):
        cleaned_turn, _ = sanitize_untrusted_text(turn.content.strip())
        if not cleaned_turn:
            continue
        if used_chars + len(cleaned_turn) > _MAX_HISTORY_CHARS:
            break
        used_chars += len(cleaned_turn)
        role = Role.user if turn.role == "user" else Role.assistant
        kept_history.append(ChatMessage(role=role, content=cleaned_turn))
    kept_history.reverse()

    return [
        ChatMessage(role=Role.system, content=_LEARNING_SYSTEM_PROMPT),
        *kept_history,
        ChatMessage(role=Role.user, content=user_message),
    ]


def suggest_follow_up_prompts(
    mode: str,
    *,
    truncated: bool = False,
) -> tuple[str, ...]:
    """Return contextual academic follow-up prompts without extra inference cost."""
    if truncated:
        return (
            "Continue from where you stopped without repeating",
            "Summarize the complete answer concisely in 4 bullet points",
        )
    if mode == "explain":
        return (
            "Walk me through a worked example",
            "Show the key equations or formal rules",
            "Explain this in simpler terms with an analogy",
        )
    return (
        "Show the step-by-step derivation or reasoning",
        "Give a concrete example or code snippet",
        "What common mistakes should I avoid?",
    )


class DirectAcademicLearning:
    """Executes bounded conversational academic Q&A and topic explanations."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        request_config: AIRequestConfig | None = None,
    ) -> None:
        self.gateway = gateway
        self.request_config = request_config or AIRequestConfig(
            max_tokens=_MAX_OUTPUT_TOKENS,
            temperature=0.25,
            timeout_seconds=25.0,
            total_deadline_seconds=45.0,
            max_retries=1,
        )

    def ask(
        self,
        *,
        context: TenantContext,
        request: PersonalLearningRequest,
    ) -> PersonalLearningResponse:
        messages = build_personal_learning_messages(request)
        result = self.gateway.complete(
            context,
            messages,
            config=self.request_config,
        )
        max_tok = self.request_config.max_tokens or _MAX_OUTPUT_TOKENS
        truncated = is_response_truncated(result, max_tokens=max_tok)
        return PersonalLearningResponse(
            answer=result.content.strip(),
            mode=request.mode,
            model=result.model,
            provider=result.metadata.provider,
            follow_up_prompts=suggest_follow_up_prompts(
                request.mode, truncated=truncated
            ),
            usage_tokens=result.usage.total_tokens,
            truncated=truncated,
        )
