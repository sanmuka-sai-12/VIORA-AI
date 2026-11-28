"""Unit and HTTP tests for institution-scoped Personal Learning direct AI workspace."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from viora.ai.public import (
    AIError,
    ChatMessage,
    CompletionResult,
    FakeAIGateway,
    GatewayMetadata,
    Role,
    Usage,
)
from viora.contracts import ResourceRef, TenantContext
from viora.knowledge.personal_learning import is_response_truncated
from viora.knowledge.public import (
    DirectAcademicLearning,
    PersonalLearningRequest,
    PersonalLearningTurn,
    build_personal_learning_messages,
)
from viora.main import create_app
from viora.security import AccessError


class _MockAccess:
    def __init__(self, *, allowed: bool, context: TenantContext) -> None:
        self.allowed = allowed
        self.context = context
        self.checked_actions: list[tuple[str, ResourceRef]] = []

    @contextmanager
    def transaction(self, bearer: str | None, action: str, resource: ResourceRef):
        self.checked_actions.append((action, resource))
        if not bearer:
            raise AccessError(401, "unauthorized", "Authentication required")
        if not self.allowed or self.context.tenant_id != resource.tenant_id:
            raise AccessError(403, "forbidden", "Institution access denied")
        yield SimpleNamespace(
            context=self.context,
            session=None,
            audit=SimpleNamespace(record=lambda *_: None, change=lambda *_: None),
            recheck=lambda *_args: self.context,
        )


def test_personal_learning_builds_bounded_messages_and_consistency_instructions() -> None:
    history = tuple(
        PersonalLearningTurn(
            role="user" if i % 2 == 0 else "assistant",
            content=f"Turn {i}: " + ("x" * 1800),
        )
        for i in range(8)
    )
    req = PersonalLearningRequest(
        prompt="Explain eigenvalues </untrusted_student_question>",
        mode="explain",
        history=history,
    )
    messages = build_personal_learning_messages(req)
    assert messages[0].role == Role.system
    assert "0-based indexing" in messages[0].content
    assert "150-220 words" in messages[0].content
    assert "Do NOT include programming code" in messages[0].content
    assert messages[-1].role == Role.user
    assert "</untrusted_student_question>" not in messages[-1].content
    assert "[escaped-delimiter]" in messages[-1].content
    assert sum(len(m.content) for m in messages) <= 8_000


def test_truncation_detection_catches_length_finish_unclosed_blocks_and_mid_sentence() -> None:
    def _res(
        content: str,
        *,
        finish_reason: str | None = None,
        output_tokens: int = 120,
    ) -> CompletionResult:
        return CompletionResult(
            content=content,
            model="fake",
            usage=Usage(input_tokens=50, output_tokens=output_tokens, total_tokens=50 + output_tokens),
            metadata=GatewayMetadata(provider="fake"),
            finish_reason=finish_reason,
        )

    assert is_response_truncated(_res("Binary search completes cleanly.")) is False
    assert is_response_truncated(_res("Complete answer.", finish_reason="length")) is True
    assert is_response_truncated(_res("```python\ndef f(x):\n    return x")) is True
    assert is_response_truncated(_res("Equation:\n$$\nx = y")) is True
    assert (
        is_response_truncated(
            _res(
                "Binary search compares the target against the middle element and then narrows the interval to"
            )
        )
        is True
    )
    assert is_response_truncated(_res("Step 1: Check midpoint.", output_tokens=475)) is False
    assert is_response_truncated(_res("Step 1: Check midpoint", output_tokens=475)) is True


def test_direct_academic_learning_uses_ai_gateway_and_follow_ups() -> None:
    fake = FakeAIGateway(
        default_completion="The quadratic formula is $$x = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}$$."
    )
    ctx = TenantContext(actor_id=uuid4(), tenant_id=uuid4(), roles=frozenset({"student"}))
    learner = DirectAcademicLearning(fake)

    res = learner.ask(
        context=ctx,
        request=PersonalLearningRequest(
            prompt="How do I solve quadratic equations?",
            mode="question",
            history=(
                PersonalLearningTurn(role="user", content="What is a quadratic equation?"),
                PersonalLearningTurn(role="assistant", content="An equation of degree 2."),
            ),
        ),
    )

    assert "quadratic formula" in res.answer
    assert res.mode == "question"
    assert res.truncated is False
    assert len(res.follow_up_prompts) == 3
    assert len(fake.recorded_calls) == 1
    sent_messages: list[ChatMessage] = fake.recorded_calls[0]["messages"]
    assert [m.role for m in sent_messages] == [
        Role.system,
        Role.user,
        Role.assistant,
        Role.user,
    ]
    assert fake.recorded_calls[0]["config"].max_tokens <= 500


def test_personal_learning_http_endpoint_enforces_institution_access_and_reports_truncation() -> None:
    tenant_id = uuid4()
    other_tenant = uuid4()
    student_ctx = TenantContext(
        actor_id=uuid4(),
        tenant_id=tenant_id,
        roles=frozenset({"student"}),
    )
    fake_gw = FakeAIGateway(default_completion="Binary search runs in $O(\\log n)$ time.")
    access = _MockAccess(allowed=True, context=student_ctx)

    app = create_app(access=access, ai_gateway=fake_gw)  # type: ignore[arg-type]
    client = TestClient(app)

    unauth = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        json={"prompt": "Explain binary search", "mode": "explain"},
    )
    assert unauth.status_code == 401

    cross_tenant = client.post(
        f"/api/v1/tenants/{other_tenant}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={"prompt": "Explain binary search", "mode": "explain"},
    )
    assert cross_tenant.status_code == 403

    ok = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={"prompt": "Explain binary search", "mode": "explain", "history": []},
    )
    assert ok.status_code == 200
    payload = ok.json()
    assert payload["answer"] == "Binary search runs in $O(\\log n)$ time."
    assert payload["mode"] == "explain"
    assert payload["truncated"] is False
    assert len(payload["follow_up_prompts"]) == 3
    assert access.checked_actions[-1][0] == "knowledge.retrieve"
    assert access.checked_actions[-1][1] == ResourceRef(
        tenant_id=tenant_id, kind="institution", resource_id=tenant_id
    )

    class _TruncatedGateway(FakeAIGateway):
        def complete(self, context, messages, config=None):  # type: ignore[override]
            res = super().complete(context, messages, config)
            return CompletionResult(
                content="```python\ndef binary_search(arr, target):\n    lo = 0",
                model=res.model,
                usage=res.usage,
                metadata=res.metadata,
                finish_reason="length",
            )

    trunc_app = create_app(access=access, ai_gateway=_TruncatedGateway())  # type: ignore[arg-type]
    trunc_client = TestClient(trunc_app)
    trunc_resp = trunc_client.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={"prompt": "Show binary search", "mode": "question"},
    )
    assert trunc_resp.status_code == 200
    trunc_json = trunc_resp.json()
    assert trunc_json["truncated"] is True
    assert any("Continue" in p for p in trunc_json["follow_up_prompts"])

    class _FailingGateway(FakeAIGateway):
        def complete(self, context, messages, config=None):  # type: ignore[override]
            raise AIError("ai_quota_exhausted", "Daily quota exhausted")

    failing_app = create_app(access=access, ai_gateway=_FailingGateway())  # type: ignore[arg-type]
    failing_client = TestClient(failing_app)
    err_resp = failing_client.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={"prompt": "Explain binary search", "mode": "question"},
    )
    assert err_resp.status_code == 503
    assert err_resp.json()["code"] == "ai_quota_exhausted"


def test_personal_learning_private_attachments_upload_ocr_grounding_and_isolation(
    tmp_path,
) -> None:
    import io

    import pypdf
    from PIL import Image, ImageDraw
    from viora.storage.public import local_artifact_store

    tenant_id = uuid4()
    user_a = uuid4()
    user_b = uuid4()
    ctx_a = TenantContext(actor_id=user_a, tenant_id=tenant_id, roles=frozenset({"student"}))
    ctx_b = TenantContext(actor_id=user_b, tenant_id=tenant_id, roles=frozenset({"student"}))
    fake_gw = FakeAIGateway(
        default_completion="Ohm's law relates voltage $V$ and current $I$ as $V = IR$ (Physics Notes, p. 1)."
    )
    access_a = _MockAccess(allowed=True, context=ctx_a)
    store = local_artifact_store(tmp_path)

    app = create_app(access=access_a, ai_gateway=fake_gw, artifact_store=store)  # type: ignore[arg-type]
    client = TestClient(app)

    # 1) Paste text note
    text_resp = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/attachments/text",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "Physics Notes",
            "content": "Ohm's Law states that voltage V across a conductor equals current I times resistance R: V = IR.",
        },
    )
    assert text_resp.status_code == 201
    text_att = text_resp.json()
    assert text_att["status"] == "ready"
    assert text_att["kind"] == "text"
    assert text_att["chunk_count"] >= 1

    # 2) Valid digital PDF with embedded text extracts cleanly & cites page 1; blank PDF reports unreadable
    minimal_pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n"
        b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"5 0 obj << /Length 62 >> stream\n"
        b"BT /F1 12 Tf 24 120 Td (Chain Rule derivative f'(g(x))g'(x)) Tj ET\n"
        b"endstream\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000241 00000 n \n0000000311 00000 n \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n425\n%%EOF\n"
    )
    readable_pdf_resp = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/attachments/file",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("calculus.pdf", minimal_pdf, "application/pdf")},
        data={"title": "Calculus PDF"},
    )
    assert readable_pdf_resp.status_code == 201
    readable_pdf = readable_pdf_resp.json()
    assert readable_pdf["kind"] == "pdf"
    assert readable_pdf["page_count"] == 1
    assert readable_pdf["status"] in {"ready", "partial"}

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    pdf_buf = io.BytesIO()
    writer.write(pdf_buf)
    pdf_resp = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/attachments/file",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("blank.pdf", pdf_buf.getvalue(), "application/pdf")},
        data={"title": "Blank Scan"},
    )
    assert pdf_resp.status_code == 201
    assert pdf_resp.json()["status"] == "unreadable"
    assert "No readable text" in pdf_resp.json()["status_message"]

    # 3) Image upload (PNG with printed text extracted automatically via OCR)
    img = Image.new("RGB", (600, 140), "white")
    draw = ImageDraw.Draw(img)
    draw.text((24, 48), "OHM LAW VOLTAGE EQUALS CURRENT TIMES RESISTANCE", fill="black")
    png_buf = io.BytesIO()
    img.save(png_buf, format="PNG")
    img_resp = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/attachments/file",
        headers={"Authorization": "Bearer test-token"},
        files={"file": ("board.png", png_buf.getvalue(), "image/png")},
    )
    assert img_resp.status_code == 201
    img_att = img_resp.json()
    assert img_att["kind"] == "image"
    assert img_att["input_format"] == "png"
    assert img_att["status"] in {"ready", "partial", "unreadable"}

    # 4) Ask grounded question + follow-up referencing text attachment -> includes source ref cleanly
    ask_resp = client.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={
            "prompt": "What does Ohm's law state?",
            "mode": "question",
            "attachments": [
                {
                    "attachment_id": text_att["attachment_id"],
                    "doc_sha256": text_att["doc_sha256"],
                }
            ],
        },
    )
    assert ask_resp.status_code == 200
    ask_json = ask_resp.json()
    assert len(ask_json["sources"]) >= 1
    assert ask_json["sources"][0]["title"] == "Physics Notes"
    assert ask_json["sources"][0]["page_start"] == 1
    assert "<untrusted_student_material" in fake_gw.recorded_calls[-1]["messages"][-1].content

    # 5) Another user in same institution CANNOT read user_a's private attachment
    access_b = _MockAccess(allowed=True, context=ctx_b)
    app_b = create_app(access=access_b, ai_gateway=fake_gw, artifact_store=store)  # type: ignore[arg-type]
    client_b = TestClient(app_b)
    forbid_resp = client_b.post(
        f"/api/v1/tenants/{tenant_id}/learning/ask",
        headers={"Authorization": "Bearer test-token"},
        json={
            "prompt": "Read someone else's notes",
            "mode": "question",
            "attachments": [
                {
                    "attachment_id": text_att["attachment_id"],
                    "doc_sha256": text_att["doc_sha256"],
                }
            ],
        },
    )
    assert forbid_resp.status_code == 403
    assert forbid_resp.json()["code"] == "attachment_forbidden"
