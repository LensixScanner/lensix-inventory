"""Azure Synapse Analytics gathering — workspaces.

Only the data-fetching call is included here (workspaces.list) — missing-
managed-virtual-network evaluation is left server-side.
`managed_virtual_network` is already present on the full
`Workspace.as_dict()` payload.

Edges: synapse_workspace -> subnet (in_subnet), when Data Exfiltration
Protection's managed VNet has a compute subnet configured
(`virtual_network_profile.compute_subnet_id` — already embedded in the
workspace's own list() response, no extra call needed). Emitted as read —
the subnet endpoint is owned by network.py's own gather(), a separate
module/container (see its own docstring for this pattern).

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
        raw = ws.as_dict()
        added = writer.add_resource(
            resource_type='synapse_workspace',
            region=ws.location or 'global',
            resource_id=ws.id,
            resource_name=ws.name,
            scope_id=_resource_group(ws.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            subnet_id = (raw.get('virtual_network_profile') or {}).get('compute_subnet_id')
            if subnet_id:
                writer.add_edge(from_type='synapse_workspace', from_id=ws.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
