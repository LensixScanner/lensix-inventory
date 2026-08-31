"""Azure Service Bus gathering — namespaces.

Only the data-fetching call is included here (namespaces.list) — missing-
customer-managed-key, public-network-access, local-auth/SAS-keys-enabled,
and minimum-TLS-version evaluation is left server-side. Every field that
evaluation needs (`encryption`, `public_network_access`,
`disable_local_auth`, `minimum_tls_version`) is already present on the
full `SBNamespace.as_dict()` payload.

Requires: azure-mgmt-servicebus.
"""

from ._util import resource_group as _resource_group

def get_namespaces(credential, subscription_id):
    from azure.mgmt.servicebus import ServiceBusManagementClient
    client = ServiceBusManagementClient(credential, subscription_id)
    return list(client.namespaces.list())


def gather(credential, subscription_id, writer):
    try:
        namespaces = get_namespaces(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='servicebus:namespaces', message=e)
        return

    for ns in namespaces:
        raw = ns.as_dict()
        writer.add_resource(
            resource_type='servicebus_namespace',
            region=ns.location or 'global',
            resource_id=ns.id,
            resource_name=ns.name,
            scope_id=_resource_group(ns.id),
            raw=raw,
            tags=raw.get('tags'),
        )
