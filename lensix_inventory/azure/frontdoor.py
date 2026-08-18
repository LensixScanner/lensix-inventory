"""Azure Front Door (classic) gathering.

`front_doors.list()` already returns everything needed for WAF-policy and
routing-rule evaluation (web_application_firewall_policy, routing_rules) —
that evaluation itself is left server-side. Gathered here as
`frontdoor_profile` resources.
"""

from azure.mgmt.frontdoor import FrontDoorManagementClient
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_front_doors(credential, subscription_id):
    client = FrontDoorManagementClient(credential, subscription_id)
    return list(client.front_doors.list())


def gather(credential, subscription_id, writer):
    for fd in get_front_doors(credential, subscription_id):
        writer.add_resource(
            resource_type='frontdoor_profile',
            region=getattr(fd, 'location', None) or 'global',
            resource_id=fd.id,
            resource_name=fd.name,
            scope_id=_resource_group(fd.id),
            raw=_as_dict(fd),
        )
