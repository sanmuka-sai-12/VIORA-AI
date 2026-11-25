from unittest.mock import create_autospec
from uuid import uuid4

from viora.authorization.policy import Policy
from viora.contracts import Actor, ResourceRef, TenantContext
from viora.identity.public import IdentityDirectory
from viora.institution.public import InstitutionDirectory


def test_role_claims_and_unknown_resources_never_grant_access() -> None:
    identities = create_autospec(IdentityDirectory, instance=True)
    institutions = create_autospec(InstitutionDirectory, instance=True)
    actor, tenant = uuid4(), uuid4()
    context = TenantContext(
        actor_id=actor, tenant_id=tenant, roles=frozenset({"institution_owner"})
    )
    identities.active.return_value = context
    policy = Policy(identities, institutions)
    assert not policy.permits(
        context,
        "source.read",
        ResourceRef(tenant_id=tenant, kind="source", resource_id=uuid4()),
    )
    assert not policy.permits(
        context,
        "anything",
        ResourceRef(tenant_id=tenant, kind="institution", resource_id=tenant),
    )
    identities.active.return_value = None
    assert not policy.permits(
        context,
        "institution.read",
        ResourceRef(tenant_id=tenant, kind="institution", resource_id=tenant),
    )
    identities.active.assert_called_with(Actor(actor_id=actor), tenant)
