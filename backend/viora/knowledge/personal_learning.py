"""Bounded direct academic learning service powered by AIGateway and private study attachments."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid5

from ..ai.public import (
    AIGateway,
    AIRequestConfig,
    ChatMessage,
    CompletionResult,
    Role,
)
from ..contracts import ArtifactVersionRef, TenantContext
from ..security import AccessError
from ..sources.extraction import (
    validate_and_sniff_text_input,
    validate_and_sniff_upload,
)
from ..sources.public import (
    AdaptiveDocumentExtractor,
    DocumentExtractor,
    ExtractedDocument,
    RapidOcrBackend,
    SourceIngestionLimits,
    SourceInputFormat,
    clamp_source_limits,
)
from ..storage.public import ArtifactStore, StorageError
from .chunking import chunk_extracted_document
from .mind_map import (
    MIND_MAP_SYSTEM_PROMPT,
    mind_map_to_markdown,
    parse_mind_map_response,
)
from .prompts import sanitize_untrusted_text
from .schemas import (
    ChunkingConfig,
    FlashcardDeck,
    LearningResourceSourceScope,
    LearningToolKind,
    MindMapGraph,
    PersonalLearningAttachment,
    PersonalLearningAttachmentKind,
    PersonalLearningAttachmentRef,
    PersonalLearningAttachmentStatus,
    PersonalLearningRequest,
    PersonalLearningResourceGenerateRequest,
    PersonalLearningResponse,
    PersonalLearningSourceRef,
)
from .study_tools import (
    build_flashcards_system_prompt,
    build_practice_quiz_system_prompt,
    compute_study_item_counts,
    flashcards_to_markdown,
    parse_flashcards_response,
    parse_practice_quiz_response,
    quiz_to_student_markdown,
)

_MAX_HISTORY_CHARS = 7_500
_MAX_ATTACHMENT_CHARS = 3_600
_MAX_TOTAL_TURNS = 10
_MAX_OUTPUT_TOKENS = 480

_RAW_ATTACHMENT_KIND = "pl_attachment_raw"
_DOC_ATTACHMENT_KIND = "pl_attachment_doc"
_EXTRACTED_NS = UUID("6b9c0c4e-2b8a-4e20-8f0a-7c4d0f1b9001")

_LEARNING_SYSTEM_PROMPT = (
    "You are VIORA, an academic learning assistant for students.\n"
    "Answer clearly, accurately, and concisely so your entire response fits cleanly within ~150-220 words.\n"
    "ACCURACY & CONSISTENCY:\n"
    "- Keep all worked examples, calculations, and algorithm traces 100% consistent from start to finish.\n"
    "- For array/sequence algorithms, state the indexing convention explicitly (use 0-based indexing consistently unless asked otherwise) and keep exact array values and indices aligned at every step.\n"
    "- When student study material excerpts are provided, answer from those materials first and cite the material title and page number (e.g. (Lecture Notes, p. 2)). If the uploaded material does not cover part of the question, say so clearly.\n"
    "FORMATTING:\n"
    "- Use clean markdown headings with a space (e.g. `## Concept`, `## Example`).\n"
    "- Use LaTeX math: `$...$` for inline math and `$$...$$` on its own lines for display equations.\n"
    "- Always finish with a complete final sentence. If a topic is broad, cover the core cleanly and let the student ask follow-ups.\n"
    "CODE RULE:\n"
    "- Do NOT include programming code, pseudocode, or code blocks unless the student explicitly asks for code or software implementation. Explain math, science, and theory purely with concepts, equations, and words."
)

_STUDY_NOTES_SYSTEM_PROMPT = (
    "You are VIORA, an academic study assistant creating structured, exam-ready Study Notes.\n"
    "Keep total length concise (~180-240 words) so all sections finish cleanly before token limits.\n"
    "SECTIONS (use exact markdown headings with spaces):\n"
    "## Key Concepts & Definitions\n"
    "## Formulas & Core Rules\n"
    "## Important Points\n"
    "## Exam-Ready Summary\n"
    "RULES:\n"
    "- Ground notes directly in the provided topic, conversation, and study material excerpts; cite uploaded sources by title and page (e.g. (Notes, p. 1)).\n"
    "- Use LaTeX `$...$` for inline math and `$$...$$` on its own lines for key formulas (omit formula section if strictly non-mathematical humanities).\n"
    "- Maintain exact consistency (including 0-based indexing for array algorithms).\n"
    "- Finish cleanly with a complete final sentence.\n"
    "- Do NOT include programming code unless the topic/material is explicitly about programming or software implementation."
)

_ACADEMIC_INSIGHTS_SYSTEM_PROMPT = (
    "You are VIORA, an academic tutor distilling deep Academic Insights and exam takeaways.\n"
    "Keep total length concise (~180-240 words) so all insights finish cleanly before token limits.\n"
    "SECTIONS (use exact markdown headings with spaces):\n"
    "## Core Insights & Connections\n"
    "## Key Definitions & Formulas\n"
    "## Common Pitfalls to Avoid\n"
    "## High-Yield Exam Takeaways\n"
    "RULES:\n"
    "- Synthesize from the student's topic, conversation, and uploaded study materials; cite uploaded materials by title and page (e.g. (Notes, p. 1)).\n"
    "- Use LaTeX `$...$` for inline math and `$$...$$` on its own lines for formulas.\n"
    "- Finish cleanly with a complete final sentence.\n"
    "- Do NOT include programming code unless the topic/material is explicitly about programming or software implementation."
)

_TRAILING_INCOMPLETE_WORD_RE = re.compile(
    r"(?:^|\s)(?:and|or|but|the|a|an|to|of|in|on|at|for|with|by|from|as|is|are|was|were|be|that|which|where|when|while|if|then|than|using|into|because)$",
    re.IGNORECASE,
)

_WORD_TOKEN_RE = re.compile(r"[a-z0-9]{2,}", re.IGNORECASE)

_ATTACHMENT_CHUNKING_CONFIG = ChunkingConfig(
    single_pass_max_tokens=420,
    chunk_max_tokens=260,
    chunk_min_tokens=40,
    overlap_tokens=24,
    max_chunks=32,
)


def _extracted_version_id(attachment_id: UUID) -> UUID:
    return uuid5(_EXTRACTED_NS, str(attachment_id))


def _attachment_kind(input_format: SourceInputFormat) -> PersonalLearningAttachmentKind:
    if input_format in {"digital_pdf", "scanned_pdf"}:
        return "pdf"
    if input_format in {"png", "jpeg"}:
        return "image"
    return "text"


def _raw_content_type(input_format: SourceInputFormat) -> str:
    if input_format in {"digital_pdf", "scanned_pdf"}:
        return "application/pdf"
    if input_format == "png":
        return "image/png"
    if input_format == "jpeg":
        return "image/jpeg"
    return "text/plain"


def _collapse_preview(text: str, limit: int = 180) -> str | None:
    collapsed = " ".join(text.split()).strip()
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _summarize_extraction(
    *,
    doc: ExtractedDocument,
    input_format: SourceInputFormat,
    chunk_count: int,
    limits: SourceIngestionLimits,
) -> tuple[PersonalLearningAttachmentStatus, str]:
    page_count = max(1, len(doc.pages)) if doc.pages else (1 if chunk_count > 0 else 0)
    clean_chars = len(doc.normalized_text.strip())
    used_ocr = (
        input_format in {"png", "jpeg", "scanned_pdf"}
        or any(b.provenance.method == "ocr" for b in doc.text_blocks)
        or any(p.classification == "ocr_processed" for p in doc.pages)
    )

    if (
        doc.quality.overall_status in {"unreadable", "ocr_required"}
        or clean_chars < limits.min_text_chars
        or chunk_count == 0
    ):
        kind_label = (
            "image"
            if input_format in {"png", "jpeg"}
            else ("PDF" if input_format in {"digital_pdf", "scanned_pdf"} else "note")
        )
        return (
            "unreadable",
            f"No readable text found in this {kind_label}. Try a clearer scan or paste text directly.",
        )

    has_warnings = (
        doc.quality.overall_status == "partial_with_warnings"
        or doc.quality.pages_needing_ocr > 0
        or doc.quality.pages_uncertain > 0
    )
    if has_warnings:
        return (
            "partial",
            f"Partially extracted ({page_count} page{'s' if page_count != 1 else ''}) — some regions could not be read clearly.",
        )

    if input_format in {"png", "jpeg"}:
        return ("ready", "Extracted text from image via OCR.")
    if input_format in {"digital_pdf", "scanned_pdf"}:
        via = " via OCR" if used_ocr else ""
        return (
            "ready",
            f"Extracted {page_count} page{'s' if page_count != 1 else ''}{via}.",
        )
    return ("ready", "Text material processed and ready.")


def _default_extractor(*, limits: SourceIngestionLimits, enable_ocr: bool) -> AdaptiveDocumentExtractor:
    ocr_backend = RapidOcrBackend() if enable_ocr else None
    return AdaptiveDocumentExtractor(
        ocr_backend=ocr_backend,
        max_ocr_pages=min(limits.max_pdf_pages, 10),
        page_workers=1,
        isolate_untrusted_parse=False,
    )


class PersonalLearningAttachmentService:
    """Extracts, OCRs, and stores private per-student study attachments in ArtifactStore."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        limits: SourceIngestionLimits | None = None,
        enable_ocr: bool = True,
        extractor: DocumentExtractor | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.limits = limits or clamp_source_limits()
        self.enable_ocr = enable_ocr
        self.extractor: DocumentExtractor = extractor or _default_extractor(
            limits=self.limits,
            enable_ocr=enable_ocr,
        )

    def ingest_file(
        self,
        *,
        context: TenantContext,
        raw_bytes: bytes,
        original_filename: str | None,
        claimed_content_type: str | None,
        title: str | None = None,
    ) -> PersonalLearningAttachment:
        resolved_format, checked_bytes, sha256_hex, safe_name = validate_and_sniff_upload(
            raw_bytes=raw_bytes,
            declared_format=None,
            original_filename=original_filename,
            claimed_content_type=claimed_content_type,
            limits=self.limits,
        )
        resolved_title = (
            (title or "").strip()
            or safe_name
            or ("Uploaded Image" if resolved_format in {"png", "jpeg"} else "Uploaded Material")
        )[:200]
        return self._extract_and_store(
            context=context,
            input_format=resolved_format,
            raw_bytes=checked_bytes,
            sha256_hex=sha256_hex,
            title=resolved_title,
            original_filename=safe_name,
        )

    def ingest_text(
        self,
        *,
        context: TenantContext,
        title: str | None,
        content: str,
    ) -> PersonalLearningAttachment:
        resolved_format, checked_bytes, sha256_hex, _ = validate_and_sniff_text_input(
            input_format="typed_notes",
            content=content,
            original_filename=None,
            limits=self.limits,
        )
        resolved_title = ((title or "").strip() or "Study Notes")[:200]
        return self._extract_and_store(
            context=context,
            input_format=resolved_format,
            raw_bytes=checked_bytes,
            sha256_hex=sha256_hex,
            title=resolved_title,
            original_filename=None,
        )

    def _extract_and_store(
        self,
        *,
        context: TenantContext,
        input_format: SourceInputFormat,
        raw_bytes: bytes,
        sha256_hex: str,
        title: str,
        original_filename: str | None,
    ) -> PersonalLearningAttachment:
        attachment_id = uuid4()
        extracted_version_id = _extracted_version_id(attachment_id)
        try:
            self.artifact_store.put_bytes(
                context,
                raw_bytes,
                kind=_RAW_ATTACHMENT_KIND,
                resource_id=attachment_id,
                content_type=_raw_content_type(input_format),
                artifact_id=attachment_id,
                version_id=attachment_id,
                max_bytes=self.limits.max_bytes,
            )
        except StorageError as exc:
            raise AccessError(500, exc.code, exc.message) from None

        doc = self.extractor.extract(
            tenant_id=context.tenant_id,
            source_id=attachment_id,
            version_id=attachment_id,
            source_version=1,
            source_sha256=sha256_hex,
            input_format=input_format,
            title=title,
            raw_bytes=raw_bytes,
            limits=self.limits,
        )

        clean_text = doc.normalized_text.strip()
        chunks = (
            chunk_extracted_document(doc, config=_ATTACHMENT_CHUNKING_CONFIG).chunks
            if clean_text
            else ()
        )
        status, status_message = _summarize_extraction(
            doc=doc,
            input_format=input_format,
            chunk_count=len(chunks),
            limits=self.limits,
        )
        created_at = datetime.now(UTC)

        chunk_records = [
            {
                "chunk_index": chunk.chunk_index,
                "page_start": chunk.page_start,
                "page_end": chunk.page_end,
                "section_title": chunk.heading_title,
                "text": chunk.rendered_text.strip(),
            }
            for chunk in chunks
            if chunk.rendered_text.strip()
        ]
        # Fallback if text blocks existed without produced chunks
        if not chunk_records and clean_text and status != "unreadable":
            chunk_records.append(
                {
                    "chunk_index": 0,
                    "page_start": 1,
                    "page_end": max(1, len(doc.pages)),
                    "section_title": None,
                    "text": clean_text[:1200],
                }
            )

        index_payload = {
            "owner_user_id": str(context.actor_id),
            "tenant_id": str(context.tenant_id),
            "attachment_id": str(attachment_id),
            "title": title,
            "original_filename": original_filename,
            "kind": _attachment_kind(input_format),
            "input_format": input_format,
            "status": status,
            "status_message": status_message,
            "page_count": len(doc.pages) if doc.pages else (1 if clean_text else 0),
            "chunk_count": len(chunk_records),
            "char_count": len(clean_text),
            "preview_excerpt": _collapse_preview(clean_text),
            "created_at": created_at.isoformat(),
            "chunks": chunk_records,
        }
        index_bytes = json.dumps(index_payload, ensure_ascii=False).encode("utf-8")

        try:
            stored_doc = self.artifact_store.put_bytes(
                context,
                index_bytes,
                kind=_DOC_ATTACHMENT_KIND,
                resource_id=attachment_id,
                content_type="application/json",
                artifact_id=attachment_id,
                version_id=extracted_version_id,
                max_bytes=self.limits.max_bytes,
            )
        except StorageError as exc:
            raise AccessError(500, exc.code, exc.message) from None

        return PersonalLearningAttachment(
            attachment_id=attachment_id,
            doc_sha256=stored_doc.metadata.ref.sha256,
            title=title,
            original_filename=original_filename,
            kind=_attachment_kind(input_format),
            input_format=input_format,
            status=status,
            status_message=status_message,
            page_count=int(index_payload["page_count"]),
            chunk_count=len(chunk_records),
            char_count=len(clean_text),
            preview_excerpt=index_payload["preview_excerpt"],
            created_at=created_at,
        )

    def load_context_excerpts(
        self,
        *,
        context: TenantContext,
        attachments: Sequence[PersonalLearningAttachmentRef],
        prompt: str,
        max_chars: int = _MAX_ATTACHMENT_CHARS,
    ) -> tuple[str, tuple[PersonalLearningSourceRef, ...]]:
        """Load owner-verified extracted chunks matching the question within budget."""
        if not attachments:
            return "", ()

        prompt_terms = {t.lower() for t in _WORD_TOKEN_RE.findall(prompt)}
        candidates: list[tuple[int, int, int, UUID, str, dict[str, Any]]] = []
        seen_ids: set[UUID] = set()

        for att_order, ref in enumerate(attachments):
            if ref.attachment_id in seen_ids:
                continue
            seen_ids.add(ref.attachment_id)
            version_ref = ArtifactVersionRef(
                tenant_id=context.tenant_id,
                kind=_DOC_ATTACHMENT_KIND,
                resource_id=ref.attachment_id,
                version_id=_extracted_version_id(ref.attachment_id),
                sha256=ref.doc_sha256,
            )
            try:
                raw_json = self.artifact_store.read(context, version_ref)
                payload = json.loads(raw_json.decode("utf-8"))
            except (StorageError, UnicodeDecodeError, json.JSONDecodeError):
                raise AccessError(
                    404,
                    "attachment_not_found",
                    "One or more study attachments could not be found in your workspace",
                ) from None

            if (
                payload.get("owner_user_id") != str(context.actor_id)
                or payload.get("tenant_id") != str(context.tenant_id)
            ):
                raise AccessError(
                    403,
                    "attachment_forbidden",
                    "Attachment belongs to a different user or institution",
                )

            att_title = str(payload.get("title") or "Study Material")
            raw_chunks = payload.get("chunks") or []
            for c_idx, chunk in enumerate(raw_chunks):
                text = str(chunk.get("text") or "").strip()
                if not text:
                    continue
                chunk_terms = {t.lower() for t in _WORD_TOKEN_RE.findall(text)}
                overlap = len(prompt_terms & chunk_terms) if prompt_terms else 0
                candidates.append((overlap, att_order, c_idx, ref.attachment_id, att_title, chunk))

        if not candidates:
            return "", ()

        # Ensure first attachment chunk gets priority if lexical overlap ties,
        # while pulling highest-relevance chunks across all active attachments.
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))

        selected_blocks: list[str] = []
        source_refs: list[PersonalLearningSourceRef] = []
        used_chars = 0

        for _, _, _, att_id, att_title, chunk in candidates:
            raw_chunk_text = str(chunk.get("text") or "").strip()
            safe_title, _ = sanitize_untrusted_text(att_title)
            safe_body, _ = sanitize_untrusted_text(raw_chunk_text[:1_200])
            page_start = max(1, int(chunk.get("page_start") or 1))
            page_end = max(page_start, int(chunk.get("page_end") or page_start))
            page_label = (
                f"p. {page_start}"
                if page_start == page_end
                else f"pp. {page_start}-{page_end}"
            )
            section_title_raw = chunk.get("section_title")
            section_title = str(section_title_raw).strip() if section_title_raw else None

            header = f'<untrusted_student_material title="{safe_title}" pages="{page_label}"'
            if section_title:
                safe_sec, _ = sanitize_untrusted_text(section_title)
                header += f' section="{safe_sec}"'
            block = f"{header}>\n{safe_body}\n</untrusted_student_material>"

            if used_chars + len(block) > max_chars:
                remaining = max_chars - used_chars
                if not selected_blocks and remaining > 160:
                    clipped_body = safe_body[: remaining - len(header) - 40].rstrip() + "..."
                    block = f"{header}>\n{clipped_body}\n</untrusted_student_material>"
                    selected_blocks.append(block)
                    source_refs.append(
                        PersonalLearningSourceRef(
                            attachment_id=att_id,
                            title=att_title,
                            page_start=page_start,
                            page_end=page_end,
                            section_title=section_title,
                            excerpt=_collapse_preview(raw_chunk_text, 150) or "",
                        )
                    )
                break

            used_chars += len(block) + 1
            selected_blocks.append(block)
            source_refs.append(
                PersonalLearningSourceRef(
                    attachment_id=att_id,
                    title=att_title,
                    page_start=page_start,
                    page_end=page_end,
                    section_title=section_title,
                    excerpt=_collapse_preview(raw_chunk_text, 150) or "",
                )
            )

        return "\n".join(selected_blocks), tuple(source_refs)


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
    *,
    attachment_context: str = "",
) -> list[ChatMessage]:
    """Construct bounded chat messages from sanitized prompt, materials, and recent history."""
    clean_prompt, _ = sanitize_untrusted_text(req.prompt.strip())
    mode_prefix = (
        "Explain this academic topic concisely with core intuition, key rule/equation, and one short consistent worked example (only show code if asked):\n"
        if req.mode == "explain"
        else ""
    )
    material_block = (
        f"Student uploaded study materials:\n{attachment_context}\n\n"
        if attachment_context
        else ""
    )
    user_message = f"{material_block}{mode_prefix}{clean_prompt}".strip()

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
    has_attachments: bool = False,
) -> tuple[str, ...]:
    """Return contextual academic follow-up prompts without extra inference cost."""
    if truncated:
        return (
            "Continue from where you stopped without repeating",
            "Summarize the complete answer concisely in 4 bullet points",
        )
    if has_attachments:
        return (
            "Summarize the main points from my material",
            "Walk me through a worked example based on this material",
            "Explain the most challenging concept in simpler terms",
        )
    if mode == "explain":
        return (
            "Walk me through a worked example",
            "Show the key equations or formal rules",
            "Explain this in simpler terms with an analogy",
        )
    return (
        "Show the step-by-step derivation or reasoning",
        "Give a concrete worked example",
        "What common mistakes should I avoid?",
    )


def _study_resource_title(
    tool_kind: LearningToolKind,
    *,
    topic: str | None,
    session_title: str | None,
    sources: Sequence[PersonalLearningSourceRef],
) -> str:
    if tool_kind == "study_notes":
        prefix = "Study Notes"
    elif tool_kind == "academic_insights":
        prefix = "Academic Insights"
    elif tool_kind == "mind_map":
        prefix = "Mind Map"
    elif tool_kind == "flashcards":
        prefix = "Flashcards"
    else:
        prefix = "Practice Quiz"
    label = (topic or "").strip() or (
        session_title.strip()
        if session_title and session_title.strip() != "New Study Session"
        else ""
    )
    if not label and sources:
        label = sources[0].title
    if not label:
        return prefix
    short = label if len(label) <= 72 else label[:69].rstrip() + "..."
    return f"{prefix}: {short}"


def _resolve_study_scope(
    *,
    has_topic: bool,
    has_conversation: bool,
    has_materials: bool,
) -> LearningResourceSourceScope:
    active_dims = int(has_topic) + int(has_conversation) + int(has_materials)
    if active_dims >= 2:
        return "combined"
    if has_materials:
        return "materials"
    if has_conversation:
        return "conversation"
    return "topic"


def build_study_resource_messages(
    req: PersonalLearningResourceGenerateRequest,
    *,
    attachment_context: str = "",
) -> list[ChatMessage]:
    """Build bounded chat messages for explicit Study Notes or Academic Insights generation."""
    clean_topic, _ = sanitize_untrusted_text((req.topic or "").strip())
    kept_turns: list[str] = []
    if req.include_conversation and req.history:
        budget = 3_200
        used = 0
        for turn in reversed(req.history[-8:]):
            clean_turn, _ = sanitize_untrusted_text(turn.content.strip())
            if not clean_turn:
                continue
            speaker = "Student" if turn.role == "user" else "Assistant"
            line = f"{speaker}: {clean_turn[:900]}"
            if used + len(line) > budget:
                break
            used += len(line)
            kept_turns.append(line)
        kept_turns.reverse()

    if not clean_topic and not kept_turns and not attachment_context:
        raise AccessError(
            422,
            "empty_study_context",
            "Provide a topic, have a conversation, or attach study materials before generating Study Notes or Academic Insights.",
        )

    context_chars = len(clean_topic) + sum(len(t) for t in kept_turns) + len(attachment_context)
    card_count, quiz_count = compute_study_item_counts(context_chars)

    if req.tool_kind == "study_notes":
        sys_prompt = _STUDY_NOTES_SYSTEM_PROMPT
        action_line = "Generate concise, exam-ready Study Notes covering key concepts, definitions, formulas, important points, and an exam summary."
    elif req.tool_kind == "academic_insights":
        sys_prompt = _ACADEMIC_INSIGHTS_SYSTEM_PROMPT
        action_line = "Generate concise, high-yield Academic Insights covering core connections, key definitions, formulas, common pitfalls, and exam takeaways."
    elif req.tool_kind == "mind_map":
        sys_prompt = MIND_MAP_SYSTEM_PROMPT
        action_line = "Generate a structured hierarchical concept mind map in JSON with clear parent-child relationships and concise node explanations."
    elif req.tool_kind == "flashcards":
        sys_prompt = build_flashcards_system_prompt(card_count)
        action_line = f"Generate {card_count} concise, high-yield study flashcards as compact JSON."
    else:
        sys_prompt = build_practice_quiz_system_prompt(req.difficulty, question_count=quiz_count)
        action_line = f"Generate a {quiz_count}-question {req.difficulty}-difficulty practice quiz as compact JSON."

    parts: list[str] = []
    if clean_topic:
        parts.append(f"Selected topic or focus:\n{clean_topic}")
    if kept_turns:
        parts.append("Recent conversation context:\n" + "\n".join(kept_turns))
    if attachment_context:
        parts.append(f"Student uploaded study materials:\n{attachment_context}")

    parts.append(action_line)
    user_payload = "\n\n".join(parts)

    # Enforce hard Worker input budget safety ceiling cleanly.
    max_user_chars = max(800, _MAX_HISTORY_CHARS - len(sys_prompt))
    if len(user_payload) > max_user_chars:
        user_payload = user_payload[: max_user_chars - 3].rstrip() + "..."

    return [
        ChatMessage(role=Role.system, content=sys_prompt),
        ChatMessage(role=Role.user, content=user_payload),
    ]


class DirectAcademicLearning:
    """Executes bounded conversational academic Q&A, explanations, and study resources."""

    def __init__(
        self,
        gateway: AIGateway,
        *,
        request_config: AIRequestConfig | None = None,
        attachment_service: PersonalLearningAttachmentService | None = None,
    ) -> None:
        self.gateway = gateway
        self.attachment_service = attachment_service
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
        attachment_context = ""
        sources: tuple[PersonalLearningSourceRef, ...] = ()
        if request.attachments:
            if self.attachment_service is None:
                raise AccessError(
                    503,
                    "attachment_storage_unavailable",
                    "Study attachment storage is not configured",
                )
            attachment_context, sources = self.attachment_service.load_context_excerpts(
                context=context,
                attachments=request.attachments,
                prompt=request.prompt,
            )

        messages = build_personal_learning_messages(
            request,
            attachment_context=attachment_context,
        )
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
                request.mode,
                truncated=truncated,
                has_attachments=bool(sources),
            ),
            sources=sources,
            usage_tokens=result.usage.total_tokens,
            truncated=truncated,
        )

    def generate_study_resource(
        self,
        *,
        context: TenantContext,
        request: PersonalLearningResourceGenerateRequest,
        session_title: str | None = None,
    ) -> dict[str, Any]:
        attachment_context = ""
        sources: tuple[PersonalLearningSourceRef, ...] = ()
        query_hint = (request.topic or "").strip()
        if not query_hint and request.history:
            query_hint = request.history[-1].content
        if request.attachments:
            if self.attachment_service is None:
                raise AccessError(
                    503,
                    "attachment_storage_unavailable",
                    "Study attachment storage is not configured",
                )
            attachment_context, sources = self.attachment_service.load_context_excerpts(
                context=context,
                attachments=request.attachments,
                prompt=query_hint or session_title or "key concepts summary definitions formulas",
            )

        messages = build_study_resource_messages(
            request,
            attachment_context=attachment_context,
        )
        scope = _resolve_study_scope(
            has_topic=bool((request.topic or "").strip()),
            has_conversation=bool(request.include_conversation and request.history),
            has_materials=bool(sources),
        )
        title = _study_resource_title(
            request.tool_kind,
            topic=request.topic,
            session_title=session_title,
            sources=sources,
        )
        if request.tool_kind == "mind_map":
            result = self.gateway.complete(
                context,
                messages,
                config=self.request_config,
            )
            graph: MindMapGraph = parse_mind_map_response(
                result.content,
                finish_reason=result.finish_reason,
            )
            return {
                "tool_kind": request.tool_kind,
                "source_scope": scope,
                "title": (
                    graph.title
                    if not (request.topic or "").strip() and graph.title
                    else title
                ),
                "focus_topic": (request.topic or "").strip() or None,
                "content": mind_map_to_markdown(graph),
                "structured_data": graph.model_dump(mode="json"),
                "sources": sources,
                "model": result.model,
                "provider": result.metadata.provider,
                "usage_tokens": result.usage.total_tokens,
                "truncated": False,
            }

        if request.tool_kind == "flashcards":
            result = self.gateway.complete(
                context,
                messages,
                config=self.request_config,
            )
            deck: FlashcardDeck = parse_flashcards_response(
                result.content,
                finish_reason=result.finish_reason,
            )
            return {
                "tool_kind": request.tool_kind,
                "source_scope": scope,
                "title": (
                    deck.title
                    if not (request.topic or "").strip() and deck.title
                    else title
                ),
                "focus_topic": (request.topic or "").strip() or None,
                "content": flashcards_to_markdown(deck),
                "structured_data": deck.model_dump(mode="json"),
                "sources": sources,
                "model": result.model,
                "provider": result.metadata.provider,
                "usage_tokens": result.usage.total_tokens,
                "truncated": False,
            }

        if request.tool_kind == "practice_quiz":
            result = self.gateway.complete(
                context,
                messages,
                config=self.request_config,
            )
            quiz_data = parse_practice_quiz_response(
                result.content,
                difficulty=request.difficulty,
                finish_reason=result.finish_reason,
            )
            return {
                "tool_kind": request.tool_kind,
                "source_scope": scope,
                "title": (
                    str(quiz_data["title"])
                    if not (request.topic or "").strip() and quiz_data.get("title")
                    else title
                ),
                "focus_topic": (request.topic or "").strip() or None,
                "content": quiz_to_student_markdown(quiz_data),
                "structured_data": quiz_data,
                "sources": sources,
                "model": result.model,
                "provider": result.metadata.provider,
                "usage_tokens": result.usage.total_tokens,
                "truncated": False,
            }

        result = self.gateway.complete(
            context,
            messages,
            config=self.request_config,
        )
        max_tok = self.request_config.max_tokens or _MAX_OUTPUT_TOKENS
        truncated = is_response_truncated(result, max_tokens=max_tok)
        return {
            "tool_kind": request.tool_kind,
            "source_scope": scope,
            "title": title,
            "focus_topic": (request.topic or "").strip() or None,
            "content": result.content.strip(),
            "structured_data": None,
            "sources": sources,
            "model": result.model,
            "provider": result.metadata.provider,
            "usage_tokens": result.usage.total_tokens,
            "truncated": truncated,
        }
