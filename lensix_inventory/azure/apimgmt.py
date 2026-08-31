"""Azure API Management gathering.

`api_management_service.list()` already returns everything needed for
identity and TLS-protocol-flag evaluation (identity, custom_properties) —
that evaluation itself is left server-side.
"""

from azure.mgmt.apimanagement import ApiManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_services(credential, subscription_id):
    apim_client = ApiManagementClient(credential, subscription_id)
    return list(apim_client.api_management_service.list())


def gather(credential, subscription_id, writer):
    for service in get_services(credential, subscription_id):
        raw = _as_dict(service)
        writer.add_resource(
            resource_type='apimgmt_service',
            region=service.location or 'global',
            resource_id=service.id,
            resource_name=service.name,
            scope_id=_resource_group(service.id),
            raw=raw,
            tags=raw.get('tags'),
        )
