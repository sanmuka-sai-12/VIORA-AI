from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session
from viora.identity.public import AccountStatus, IdentityDirectory, UserSummary
from viora.institution.public import institution_directory


def test_user_contract_excludes_credentials_and_global_roles() -> None:
    summary = UserSummary(
        user_id=uuid4(), display_name="Ada", status=AccountStatus.active
    )
    assert set(summary.model_dump()) == {"user_id", "display_name", "status"}
    for field in ("password_hash", "verified_email", "role", "tenant_id"):
        with pytest.raises(ValidationError):
            UserSummary.model_validate({**summary.model_dump(), field: "private"})


def test_assignment_resolution_uses_injected_role_directory() -> None:
    tenant, user, offering = uuid4(), uuid4(), uuid4()
    identities = create_autospec(IdentityDirectory, instance=True, spec_set=True)
    identities.applicable_roles.return_value = frozenset()
    session = create_autospec(Session, instance=True, spec_set=True)
    session.scalar.return_value = True
    directory = institution_directory(session, identities)
    assert directory.teacher_assignment(tenant, user, offering) is None
    identities.applicable_roles.assert_called_once_with(user, tenant)
