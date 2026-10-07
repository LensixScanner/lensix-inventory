"""Azure custom role definition and management lock gathering.

`role_definitions.list(scope)` (filtered to role_type == 'CustomRole')
already returns everything needed for wildcard-action and owner-equivalent-
action evaluation (permissions/actions lists) — that evaluation itself is
left server-side. Gathered here as `role_definition` resources, ordinary
listable resources with their own id/name.

Management locks (Microsoft.Authorization/locks — a DIFFERENT SDK package,
azure-mgmt-resource's ManagementLockClient, despite sharing the
Microsoft.Authorization provider namespace with role definitions above)
are gathered the same way, as `authorization_lock` resources — see
get_management_locks()'s own docstring for the one-call-gets-every-scope
behavior this relies on, and lock_scope()'s docstring for how a lock's own
`.id` is turned into the scope (subscription/resource-group/resource) it
actually protects. vm.py's own gather() is the only consumer of
lock_scope() today (see that module's `_ProtectedByDeletionLock` stamp),
but it's exposed here, not privately in vm.py, since a lock can scope to
any resource type, not just VMs.
"""

from azure.mgmt.authorization import AuthorizationManagementClient
from azure.mgmt.resource.locks import ManagementLockClient
from ._util import resource_group as _resource_group, is_resource_group_scope as _is_resource_group_scope, as_dict as _as_dict

# A lock at this level blocks deletion outright (CanNotDelete) or blocks
# every write including delete (ReadOnly) — NotSpecified is a valid wire
# value too (an unset/legacy lock) but doesn't actually protect anything.
DELETION_LOCK_LEVELS = frozenset({'CanNotDelete', 'ReadOnly'})


def get_custom_role_definitions(credential, subscription_id):
    auth_client = AuthorizationManagementClient(credential, subscription_id)
    scope = f'/subscriptions/{subscription_id}'
    return [r for r in auth_client.role_definitions.list(scope) if r.role_type == 'CustomRole']


def get_management_locks(credential, subscription_id):
    """One call, every lock in the subscription regardless of scope —
    `ManagementLocksOperations.list_at_subscription_level()` is documented
    as "gets all the management locks for a subscription", not "locks
    scoped to the subscription itself": a lock someone applied directly to
    a single resource group or resource is still returned here, with its
    own `.id` revealing exactly which scope it protects (see lock_scope()
    below) — no separate list_at_resource_group_level()/
    list_at_resource_level() fan-out needed."""
    lock_client = ManagementLockClient(credential, subscription_id)
    return list(lock_client.management_locks.list_at_subscription_level())


def lock_scope(lock_id):
    """A management lock's own `.id` is the LOCK's resource id, not its
    target's — e.g. a lock on a VM looks like
    `/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Compute/
    virtualMachines/{vm}/providers/Microsoft.Authorization/locks/{name}`.
    Stripping the trailing `/providers/Microsoft.Authorization/locks/
    {name}` suffix recovers the exact scope the lock actually applies
    to — uniformly for all three lock levels, since a subscription-level
    lock's id is just `/subscriptions/{sub}/providers/
    Microsoft.Authorization/locks/{name}` and a resource-group-level
    lock's is `/subscriptions/{sub}/resourceGroups/{rg}/providers/
    Microsoft.Authorization/locks/{name}` — the same suffix, just a
    shorter prefix. Returns None if the id doesn't match the expected
    shape (defensive; every real lock's id does)."""
    marker = '/providers/microsoft.authorization/locks/'
    idx = (lock_id or '').lower().find(marker)
    return lock_id[:idx] if idx >= 0 else None


def gather(credential, subscription_id, writer):
    # Two independent fetches — isolate each so one's failure (e.g. a
    # subscription that grants roleDefinitions/read but not locks/read,
    # or vice versa) doesn't discard the other, same discipline as every
    # other multi-fetch gather() in this codebase (see vm.py/account.py's
    # own module docstrings).
    #
    # No tags= here (unlike most other Azure gather modules): RBAC role
    # definitions (Microsoft.Authorization/roleDefinitions) are a
    # control-plane object, not an ARM resource with the usual `tags`
    # property — confirmed absent from RoleDefinition's own attribute map
    # — so there's genuinely nothing to pass through, matching AWS's
    # iam_group/iam_server_certificate N/A precedent (see
    # docs/tag-suppressions.md).
    try:
        role_defs = get_custom_role_definitions(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='authorization:role_definitions', message=e)
        role_defs = []
    for role_def in role_defs:
        writer.add_resource(
            resource_type='role_definition',
            region='global',
            resource_id=role_def.id,
            resource_name=role_def.role_name or role_def.name,
            scope_id=_resource_group(role_def.id),
            raw=_as_dict(role_def),
        )

    # Management locks: also no tags= (ManagementLockObject has no tags
    # property — it's a control-plane guard on another resource, not an
    # ARM resource of its own in any taggable sense). Edge to
    # resource_group only when the lock's own scope is EXACTLY a
    # resource-group path — same is_resource_group_scope() gate and same
    # reasoning as policy.py's own policy_assignment -> resource_group
    # edge (a resource-level scope would need per-resource-type
    # disambiguation to link correctly; vm.py's own gather() does that
    # disambiguation for the one resource type — vm — it actually owns,
    # via its own management_lock -> vm (protects) edge, same split as
    # rsv.py/vm.py's existing recovery_services_vault -> vm precedent).
    try:
        locks = get_management_locks(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='authorization:management_locks', message=e)
        locks = []
    for lock in locks:
        added = writer.add_resource(
            resource_type='authorization_lock',
            region='global',
            resource_id=lock.id,
            resource_name=lock.name,
            scope_id=_resource_group(lock.id),
            raw=_as_dict(lock),
        )
        if added:
            scope = lock_scope(lock.id)
            if _is_resource_group_scope(scope):
                writer.add_edge(from_type='authorization_lock', from_id=lock.id, to_type='resource_group', to_id=scope, relationship='applies_to')
