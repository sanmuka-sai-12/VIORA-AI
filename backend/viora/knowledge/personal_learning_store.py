"""PostgreSQL persistence for student-private Personal Learning sessions and library."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..contracts import TenantContext
from ..security import AccessError
from .models import (
    PersonalLearningMaterialModel,
    PersonalLearningMessageModel,
    PersonalLearningSessionModel,
)
from .schemas import (
    LearningPromptMode,
    PersonalLearningAttachment,
    PersonalLearningAttachmentRef,
    PersonalLearningMessageRecord,
    PersonalLearningSessionDetail,
    PersonalLearningSessionSummary,
    PersonalLearningSessionUpdateRequest,
    PersonalLearningSourceRef,
    PersonalLearningTurn,
)


def derive_session_title(prompt: str, *, max_len: int = 72) -> str:
    collapsed = " ".join(prompt.split()).strip()
    if not collapsed:
        return "Study Session"
    if len(collapsed) <= max_len:
        return collapsed
    clipped = collapsed[: max_len - 3].rsplit(" ", 1)[0].strip()
    return f"{clipped or collapsed[: max_len - 3].strip()}..."


def _material_dto(row: PersonalLearningMaterialModel) -> PersonalLearningAttachment:
    return PersonalLearningAttachment(
        attachment_id=row.attachment_id,
        doc_sha256=row.doc_sha256,
        title=row.title,
        original_filename=row.original_filename,
        kind=row.kind,  # type: ignore[arg-type]
        input_format=row.input_format,  # type: ignore[arg-type]
        status=row.status,  # type: ignore[arg-type]
        status_message=row.status_message,
        page_count=row.page_count,
        chunk_count=row.chunk_count,
        char_count=row.char_count,
        preview_excerpt=row.preview_excerpt,
        created_at=row.created_at,
    )


def _message_dto(row: PersonalLearningMessageModel) -> PersonalLearningMessageRecord:
    return PersonalLearningMessageRecord(
        message_id=row.message_id,
        sequence_no=row.sequence_no,
        role=row.role,  # type: ignore[arg-type]
        content=row.content,
        mode=row.mode,  # type: ignore[arg-type]
        attached_titles=tuple(str(t) for t in (row.attached_titles or ())),
        sources=tuple(
            PersonalLearningSourceRef(**src) for src in (row.sources or ())
        ),
        follow_up_prompts=tuple(str(p) for p in (row.follow_up_prompts or ())),
        truncated=bool(row.truncated),
        created_at=row.created_at,
    )


class SqlPersonalLearningRepository:
    """Tenant- and student-owned persistence backed by PostgreSQL + forced RLS."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def register_material(
        self, context: TenantContext, att: PersonalLearningAttachment
    ) -> PersonalLearningAttachment:
        row = PersonalLearningMaterialModel(
            tenant_id=context.tenant_id,
            attachment_id=att.attachment_id,
            owner_user_id=context.actor_id,
            title=att.title,
            original_filename=att.original_filename,
            kind=att.kind,
            input_format=att.input_format,
            status=att.status,
            status_message=att.status_message,
            page_count=att.page_count,
            chunk_count=att.chunk_count,
            char_count=att.char_count,
            preview_excerpt=att.preview_excerpt,
            doc_sha256=att.doc_sha256,
            created_at=att.created_at,
        )
        self.session.add(row)
        self.session.flush()
        return _material_dto(row)

    def list_materials(
        self, context: TenantContext, *, limit: int = 50
    ) -> tuple[PersonalLearningAttachment, ...]:
        rows = self.session.scalars(
            select(PersonalLearningMaterialModel)
            .where(
                PersonalLearningMaterialModel.tenant_id == context.tenant_id,
                PersonalLearningMaterialModel.owner_user_id == context.actor_id,
                PersonalLearningMaterialModel.deleted_at.is_(None),
            )
            .order_by(PersonalLearningMaterialModel.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return tuple(_material_dto(row) for row in rows)

    def resolve_material_refs(
        self, context: TenantContext, attachment_ids: Sequence[UUID]
    ) -> tuple[PersonalLearningAttachmentRef, ...]:
        unique_ids = list(dict.fromkeys(attachment_ids))[:5]
        if not unique_ids:
            return ()
        rows = self.session.scalars(
            select(PersonalLearningMaterialModel).where(
                PersonalLearningMaterialModel.tenant_id == context.tenant_id,
                PersonalLearningMaterialModel.owner_user_id == context.actor_id,
                PersonalLearningMaterialModel.attachment_id.in_(unique_ids),
                PersonalLearningMaterialModel.deleted_at.is_(None),
            )
        ).all()
        by_id = {r.attachment_id: r for r in rows}
        refs: list[PersonalLearningAttachmentRef] = []
        for aid in unique_ids:
            row = by_id.get(aid)
            if row is None:
                raise AccessError(
                    404,
                    "attachment_not_found",
                    "Requested study material was not found in your library",
                )
            refs.append(
                PersonalLearningAttachmentRef(
                    attachment_id=row.attachment_id,
                    doc_sha256=row.doc_sha256,
                )
            )
        return tuple(refs)

    def _load_materials_map(
        self, context: TenantContext, attachment_ids: Sequence[UUID]
    ) -> tuple[PersonalLearningAttachment, ...]:
        unique_ids = list(dict.fromkeys(attachment_ids))
        if not unique_ids:
            return ()
        rows = self.session.scalars(
            select(PersonalLearningMaterialModel).where(
                PersonalLearningMaterialModel.tenant_id == context.tenant_id,
                PersonalLearningMaterialModel.owner_user_id == context.actor_id,
                PersonalLearningMaterialModel.attachment_id.in_(unique_ids),
                PersonalLearningMaterialModel.deleted_at.is_(None),
            )
        ).all()
        by_id = {r.attachment_id: _material_dto(r) for r in rows}
        return tuple(by_id[aid] for aid in unique_ids if aid in by_id)

    def delete_material(self, context: TenantContext, attachment_id: UUID) -> bool:
        row = self.session.scalar(
            select(PersonalLearningMaterialModel).where(
                PersonalLearningMaterialModel.tenant_id == context.tenant_id,
                PersonalLearningMaterialModel.owner_user_id == context.actor_id,
                PersonalLearningMaterialModel.attachment_id == attachment_id,
                PersonalLearningMaterialModel.deleted_at.is_(None),
            )
        )
        if row is None:
            raise AccessError(
                404,
                "attachment_not_found",
                "Study material was not found in your library",
            )
        row.deleted_at = datetime.now(UTC)

        # Detach deleted material ID from any active personal sessions.
        att_str = str(attachment_id)
        sessions = self.session.scalars(
            select(PersonalLearningSessionModel).where(
                PersonalLearningSessionModel.tenant_id == context.tenant_id,
                PersonalLearningSessionModel.owner_user_id == context.actor_id,
                PersonalLearningSessionModel.deleted_at.is_(None),
            )
        ).all()
        for sess in sessions:
            current = [str(item) for item in (sess.active_attachment_ids or ())]
            if att_str in current:
                sess.active_attachment_ids = [item for item in current if item != att_str]
        self.session.flush()
        return True

    def _require_session(
        self, context: TenantContext, session_id: UUID
    ) -> PersonalLearningSessionModel:
        row = self.session.scalar(
            select(PersonalLearningSessionModel).where(
                PersonalLearningSessionModel.tenant_id == context.tenant_id,
                PersonalLearningSessionModel.owner_user_id == context.actor_id,
                PersonalLearningSessionModel.session_id == session_id,
                PersonalLearningSessionModel.deleted_at.is_(None),
            )
        )
        if row is None:
            raise AccessError(
                404,
                "learning_session_not_found",
                "Learning conversation was not found",
            )
        return row

    def _validated_session_attachment_ids(
        self, context: TenantContext, attachment_ids: Sequence[UUID]
    ) -> list[str]:
        if not attachment_ids:
            return []
        refs = self.resolve_material_refs(context, attachment_ids)
        return [str(ref.attachment_id) for ref in refs]

    def create_session(
        self,
        context: TenantContext,
        *,
        title: str | None = None,
        mode: LearningPromptMode = "question",
        attachment_ids: Sequence[UUID] = (),
    ) -> PersonalLearningSessionDetail:
        now = datetime.now(UTC)
        clean_title = " ".join((title or "").split()).strip() or "New Study Session"
        clean_title = clean_title[:200]
        resolved_ids = self._validated_session_attachment_ids(context, attachment_ids)
        row = PersonalLearningSessionModel(
            tenant_id=context.tenant_id,
            session_id=uuid4(),
            owner_user_id=context.actor_id,
            title=clean_title,
            mode=mode,
            active_attachment_ids=resolved_ids,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        attachments = self._load_materials_map(
            context, [UUID(s) for s in resolved_ids]
        )
        return PersonalLearningSessionDetail(
            session_id=row.session_id,
            title=row.title,
            mode=row.mode,  # type: ignore[arg-type]
            attachments=attachments,
            messages=(),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_sessions(
        self, context: TenantContext, *, limit: int = 40
    ) -> tuple[PersonalLearningSessionSummary, ...]:
        rows = self.session.scalars(
            select(PersonalLearningSessionModel)
            .where(
                PersonalLearningSessionModel.tenant_id == context.tenant_id,
                PersonalLearningSessionModel.owner_user_id == context.actor_id,
                PersonalLearningSessionModel.deleted_at.is_(None),
            )
            .order_by(PersonalLearningSessionModel.updated_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        if not rows:
            return ()

        session_ids = [r.session_id for r in rows]
        msg_rows = self.session.scalars(
            select(PersonalLearningMessageModel)
            .where(
                PersonalLearningMessageModel.tenant_id == context.tenant_id,
                PersonalLearningMessageModel.owner_user_id == context.actor_id,
                PersonalLearningMessageModel.session_id.in_(session_ids),
            )
            .order_by(
                PersonalLearningMessageModel.session_id,
                PersonalLearningMessageModel.sequence_no.desc(),
            )
        ).all()
        counts: dict[UUID, int] = {}
        previews: dict[UUID, str] = {}
        for m in msg_rows:
            counts[m.session_id] = counts.get(m.session_id, 0) + 1
            if m.session_id not in previews:
                snippet = " ".join(m.content.split()).strip()
                previews[m.session_id] = (
                    f"{snippet[:117]}..." if len(snippet) > 120 else snippet
                )

        return tuple(
            PersonalLearningSessionSummary(
                session_id=row.session_id,
                title=row.title,
                mode=row.mode,  # type: ignore[arg-type]
                message_count=counts.get(row.session_id, 0),
                last_message_preview=previews.get(row.session_id),
                attachment_ids=tuple(
                    UUID(s) for s in (row.active_attachment_ids or ())
                ),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        )

    def get_session(
        self, context: TenantContext, session_id: UUID
    ) -> PersonalLearningSessionDetail:
        row = self._require_session(context, session_id)
        msgs = self.session.scalars(
            select(PersonalLearningMessageModel)
            .where(
                PersonalLearningMessageModel.tenant_id == context.tenant_id,
                PersonalLearningMessageModel.owner_user_id == context.actor_id,
                PersonalLearningMessageModel.session_id == row.session_id,
            )
            .order_by(PersonalLearningMessageModel.sequence_no.asc())
        ).all()
        attachments = self._load_materials_map(
            context, [UUID(s) for s in (row.active_attachment_ids or ())]
        )
        return PersonalLearningSessionDetail(
            session_id=row.session_id,
            title=row.title,
            mode=row.mode,  # type: ignore[arg-type]
            attachments=attachments,
            messages=tuple(_message_dto(m) for m in msgs),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def update_session(
        self,
        context: TenantContext,
        session_id: UUID,
        req: PersonalLearningSessionUpdateRequest,
    ) -> PersonalLearningSessionDetail:
        row = self._require_session(context, session_id)
        if req.title is not None:
            clean = " ".join(req.title.split()).strip()
            if not clean:
                raise AccessError(
                    422, "invalid_session_title", "Session title cannot be empty"
                )
            row.title = clean[:200]
        if req.mode is not None:
            row.mode = req.mode
        if req.attachment_ids is not None:
            row.active_attachment_ids = self._validated_session_attachment_ids(
                context, req.attachment_ids
            )
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return self.get_session(context, session_id)

    def delete_session(self, context: TenantContext, session_id: UUID) -> bool:
        row = self._require_session(context, session_id)
        row.deleted_at = datetime.now(UTC)
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return True

    def load_recent_turns(
        self, context: TenantContext, session_id: UUID, *, limit: int = 10
    ) -> tuple[PersonalLearningTurn, ...]:
        self._require_session(context, session_id)
        rows = self.session.scalars(
            select(PersonalLearningMessageModel)
            .where(
                PersonalLearningMessageModel.tenant_id == context.tenant_id,
                PersonalLearningMessageModel.owner_user_id == context.actor_id,
                PersonalLearningMessageModel.session_id == session_id,
            )
            .order_by(PersonalLearningMessageModel.sequence_no.desc())
            .limit(max(1, min(limit, 20)))
        ).all()
        ordered = list(reversed(rows))
        return tuple(
            PersonalLearningTurn(
                role=m.role,  # type: ignore[arg-type]
                content=m.content[:4000],
            )
            for m in ordered
        )

    def append_exchange(
        self,
        context: TenantContext,
        *,
        session_id: UUID | None,
        prompt_text: str,
        mode: LearningPromptMode,
        attached_titles: Sequence[str],
        assistant_content: str,
        sources: Sequence[PersonalLearningSourceRef],
        follow_up_prompts: Sequence[str],
        truncated: bool,
        active_attachment_ids: Sequence[UUID],
    ) -> tuple[UUID, str]:
        now = datetime.now(UTC)
        resolved_ids = [
            str(aid)
            for aid in dict.fromkeys(active_attachment_ids)
        ][:5]
        if session_id is None:
            sess = PersonalLearningSessionModel(
                tenant_id=context.tenant_id,
                session_id=uuid4(),
                owner_user_id=context.actor_id,
                title=derive_session_title(prompt_text),
                mode=mode,
                active_attachment_ids=resolved_ids,
                created_at=now,
                updated_at=now,
            )
            self.session.add(sess)
            self.session.flush()
            next_seq = 1
        else:
            sess = self._require_session(context, session_id)
            if sess.title == "New Study Session":
                sess.title = derive_session_title(prompt_text)
            sess.mode = mode
            sess.active_attachment_ids = resolved_ids
            sess.updated_at = now
            max_seq = self.session.scalar(
                select(func.max(PersonalLearningMessageModel.sequence_no)).where(
                    PersonalLearningMessageModel.tenant_id == context.tenant_id,
                    PersonalLearningMessageModel.owner_user_id == context.actor_id,
                    PersonalLearningMessageModel.session_id == sess.session_id,
                )
            )
            next_seq = int(max_seq or 0) + 1

        user_msg = PersonalLearningMessageModel(
            tenant_id=context.tenant_id,
            message_id=uuid4(),
            session_id=sess.session_id,
            owner_user_id=context.actor_id,
            sequence_no=next_seq,
            role="user",
            content=prompt_text,
            mode=mode,
            attached_titles=list(attached_titles),
            sources=[],
            follow_up_prompts=[],
            truncated=False,
            created_at=now,
        )
        assistant_msg = PersonalLearningMessageModel(
            tenant_id=context.tenant_id,
            message_id=uuid4(),
            session_id=sess.session_id,
            owner_user_id=context.actor_id,
            sequence_no=next_seq + 1,
            role="assistant",
            content=assistant_content,
            mode=mode,
            attached_titles=[],
            sources=[src.model_dump(mode="json") for src in sources],
            follow_up_prompts=list(follow_up_prompts),
            truncated=truncated,
            created_at=now,
        )
        self.session.add(user_msg)
        self.session.add(assistant_msg)
        self.session.flush()
        return sess.session_id, sess.title
