"""Azure Policy gathering — subscription-level policy assignments.

Only the data-fetching call is included here (policy_assignments.list) —
missing-"allowed locations"-assignment and zero-assignments evaluation is
left server-side. Both of those findings are subscription-level, not
per-assignment, but the assignment list itself is exactly what evaluation
needs, so it's gathered here as its own `policy_assignment` resource type
to make the checks re-derivable server-side.

Requires: azure-mgmt-resource.
"""


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
        writer.add_resource(
            resource_type='policy_assignment',
            region='global',
            resource_id=assignment.id,
            resource_name=getattr(assignment, 'display_name', None) or assignment.name,
            raw=assignment.as_dict(),
        )
