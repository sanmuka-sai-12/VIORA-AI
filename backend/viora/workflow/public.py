"""Public workflow domain interface."""

from sqlalchemy.orm import Session

from . import models
from .ports import (
    ConcurrentJobConflict,
    JobCancelledError,
    JobExecutionHandler,
    StaleWorkerFenceError,
    WorkerLeaseController,
    WorkflowJobRepository,
)
from .repository import (
    SqlWorkflowDirectory,
    SqlWorkflowRepository,
    SqlWorkflowWorkerStore,
)
from .schemas import (
    ClaimedWorkflowJob,
    ExtractionTriggerRequest,
    LeaseRenewalResult,
    WorkflowJobDetail,
    WorkflowJobSubmit,
    WorkflowJobSummary,
    WorkflowOperationKind,
)
from .service import DurableWorkflowService

__all__ = [
    "ClaimedWorkflowJob",
    "ConcurrentJobConflict",
    "DurableWorkflowService",
    "ExtractionTriggerRequest",
    "JobCancelledError",
    "JobExecutionHandler",
    "LeaseRenewalResult",
    "SqlWorkflowDirectory",
    "SqlWorkflowRepository",
    "SqlWorkflowWorkerStore",
    "StaleWorkerFenceError",
    "WorkerLeaseController",
    "WorkflowJobDetail",
    "WorkflowJobRepository",
    "WorkflowJobSubmit",
    "WorkflowJobSummary",
    "WorkflowOperationKind",
    "register_models",
    "registered_table",
    "workflow_directory",
]

registered_table = models.WorkflowJob.__table__


def register_models() -> None:
    """Load SQLAlchemy workflow models into shared Alembic metadata."""
    from . import models as _models  # noqa: F401


def workflow_directory(session: Session) -> SqlWorkflowDirectory:
    return SqlWorkflowDirectory(session)
