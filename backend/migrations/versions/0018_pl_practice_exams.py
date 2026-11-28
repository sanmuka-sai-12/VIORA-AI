"""Add practice_exam tool_kind value to personal learning resources."""

from alembic import op

revision = "0018_pl_practice_exams"
down_revision = "0017_pl_flashcards_quizzes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        "tool_kind IN ('study_notes', 'academic_insights', 'mind_map', 'flashcards', 'practice_quiz', 'practice_exam')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pl_resources_tool_kind",
        "personal_learning_study_resources",
        "tool_kind IN ('study_notes', 'academic_insights', 'mind_map', 'flashcards', 'practice_quiz')",
    )
