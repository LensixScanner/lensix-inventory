"""Azure custom role definition gathering.

`role_definitions.list(scope)` (filtered to role_type == 'CustomRole')
already returns everything needed for wildcard-action and owner-equivalent-
action evaluation (permissions/actions lists) — that evaluation itself is
left server-side. Gathered here as `role_definition` resources, ordinary
listable resources with their own id/name.
"""

from azure.mgmt.authorization import AuthorizationManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_custom_role_definitions(credential, subscription_id):
    auth_client = AuthorizationManagementClient(credential, subscription_id)
    scope = f'/subscriptions/{subscription_id}'
    return [r for r in auth_client.role_definitions.list(scope) if r.role_type == 'CustomRole']


def gather(credential, subscription_id, writer):
    # No tags= here (unlike most other Azure gather modules): RBAC role
    # definitions (Microsoft.Authorization/roleDefinitions) are a
    # control-plane object, not an ARM resource with the usual `tags`
    # property — confirmed absent from RoleDefinition's own attribute map
    # — so there's genuinely nothing to pass through, matching AWS's
    # iam_group/iam_server_certificate N/A precedent (see
    # docs/tag-suppressions.md).
    for role_def in get_custom_role_definitions(credential, subscription_id):
        writer.add_resource(
            resource_type='role_definition',
            region='global',
            resource_id=role_def.id,
            resource_name=role_def.role_name or role_def.name,
            scope_id=_resource_group(role_def.id),
            raw=_as_dict(role_def),
        )
