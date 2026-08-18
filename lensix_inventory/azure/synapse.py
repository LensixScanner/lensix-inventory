"""Azure Synapse Analytics gathering — workspaces.

Only the data-fetching call is included here (workspaces.list) — missing-
managed-virtual-network evaluation is left server-side.
`managed_virtual_network` is already present on the full
`Workspace.as_dict()` payload.

Requires: azure-mgmt-synapse.
"""

from ._util import resource_group as _resource_group

def get_workspaces(credential, subscription_id):
    from azure.mgmt.synapse import SynapseManagementClient
    client = SynapseManagementClient(credential, subscription_id)
    return list(client.workspaces.list())


def gather(credential, subscription_id, writer):
    try:
        workspaces = get_workspaces(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='synapse:workspaces', message=e)
        return

    for ws in workspaces:
        writer.add_resource(
            resource_type='synapse_workspace',
            region=ws.location or 'global',
            resource_id=ws.id,
            resource_name=ws.name,
            scope_id=_resource_group(ws.id),
            raw=ws.as_dict(),
        )
