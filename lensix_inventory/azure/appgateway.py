"""Azure Application Gateway gathering.

`application_gateways.list_all()` already returns everything needed for
WAF-configuration, HTTP-listener, and SSL-policy evaluation
(web_application_firewall_configuration, http_listeners, ssl_policy) —
that evaluation itself is left server-side.
"""

from azure.mgmt.network import NetworkManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_gateways(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.application_gateways.list_all())


def gather(credential, subscription_id, writer):
    for gw in get_gateways(credential, subscription_id):
        writer.add_resource(
            resource_type='application_gateway',
            region=gw.location or 'global',
            resource_id=gw.id,
            resource_name=gw.name,
            scope_id=_resource_group(gw.id),
            raw=_as_dict(gw),
        )
