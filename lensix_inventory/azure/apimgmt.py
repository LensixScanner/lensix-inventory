"""Azure API Management gathering.

`api_management_service.list()` already returns everything needed for
identity and TLS-protocol-flag evaluation (identity, custom_properties) —
that evaluation itself is left server-side.

Edges: apimgmt_service -> subnet (in_subnet), when the service has VNet
integration configured (`virtual_network_configuration.subnet_resource_id`
— None for the common "External"/no-VNet case; already embedded in the
service's own list() response, no extra call needed). Emitted as read —
the subnet endpoint is owned by network.py's own gather(), a separate
module/container (see its own docstring for this pattern).
"""

from azure.mgmt.apimanagement import ApiManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_services(credential, subscription_id):
    apim_client = ApiManagementClient(credential, subscription_id)
    return list(apim_client.api_management_service.list())


def gather(credential, subscription_id, writer):
    for service in get_services(credential, subscription_id):
        raw = _as_dict(service)
        added = writer.add_resource(
            resource_type='apimgmt_service',
            region=service.location or 'global',
            resource_id=service.id,
            resource_name=service.name,
            scope_id=_resource_group(service.id),
            raw=raw,
            tags=raw.get('tags'),
        )
        if added:
            subnet_id = (raw.get('virtual_network_configuration') or {}).get('subnet_resource_id')
            if subnet_id:
                writer.add_edge(from_type='apimgmt_service', from_id=service.id, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
