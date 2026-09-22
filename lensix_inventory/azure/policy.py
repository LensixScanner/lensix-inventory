"""Azure Policy gathering — subscription-level policy assignments.

Only the data-fetching call is included here (policy_assignments.list) —
missing-"allowed locations"-assignment and zero-assignments evaluation is
left server-side. Both of those findings are subscription-level, not
per-assignment, but the assignment list itself is exactly what evaluation
needs, so it's gathered here as its own `policy_assignment` resource type
to make the checks re-derivable server-side.

Edges: policy_assignment -> resource_group (applies_to), only when the
assignment's own `scope` is exactly a resource-group-level ARM path
(`/subscriptions/{sub}/resourceGroups/{rg}`, no further segments) —
already embedded in the assignment's own list() response, no extra call
needed. Subscription-level scopes (just `/subscriptions/{sub}`) and
resource-level scopes (a specific resource under a resource group) are
deliberately skipped: the former has no persisted target at all in this
codebase, and the latter would need per-resource-type disambiguation
(the same problem flow_log's own target-type sniffing in
networkwatcher.py solves for exactly 3 known types — not worth
generalizing here for an occasional resource-scoped assignment). Emitted
as read — the resource_group endpoint is owned by resources.py's own
gather(), a separate module/container (see network.py's own docstring
for this general pattern).

Requires: azure-mgmt-resource.
"""

from ._util import is_resource_group_scope


def get_policy_assignments(credential, subscription_id):
    from azure.mgmt.resource import PolicyClient
    policy_client = PolicyClient(credential, subscription_id)
    return list(policy_client.policy_assignments.list())


def gather(credential, subscription_id, writer):
    try:
        assignments = get_policy_assignments(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='policy:policy_assignments', message=e)
        return

    for assignment in assignments:
        # No tags= here: PolicyAssignment (Microsoft.Authorization/
        # policyAssignments) is a control-plane object, not a
        # taggable ARM resource — confirmed absent from its own
        # attribute map, same architectural N/A as authorization.py's
        # role_definition (see that module's own comment).
        raw = assignment.as_dict()
        added = writer.add_resource(
            resource_type='policy_assignment',
            region='global',
            resource_id=assignment.id,
            resource_name=getattr(assignment, 'display_name', None) or assignment.name,
            raw=raw,
        )
        if added:
            scope = raw.get('scope')
            if is_resource_group_scope(scope):
                writer.add_edge(from_type='policy_assignment', from_id=assignment.id, to_type='resource_group', to_id=scope, relationship='applies_to')
