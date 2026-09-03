"""Microsoft Defender for Cloud gathering — subscription pricing plans and
the network interfaces IP-forwarding evaluation needs.

Two plain list calls cover every Defender-related finding:

- `SecurityCenter.pricings.list()` — subscription-wide Defender plan tiers
  (App Services, Azure SQL, Containers, Key Vault, Kubernetes, Servers,
  Storage). "Plan missing" vs. "plan present but Free tier" is finding
  evaluation over this same list, left server-side — Lensix can recompute
  it from the raw `Pricing` records gathered here.
- `NetworkManagementClient.network_interfaces.list_all()` — used for
  flagging NICs with `enable_ip_forwarding` set. Gathered as its own
  `network_interface` resource (plain list call, not evaluation); if
  another module in this tool also gathers NICs, Lensix's import can
  dedupe by resource_id/resource_type.

Both pricing plans and NICs are ordinary listable ARM resources with their
own id/name, gathered here as `defender_pricing` and `network_interface`
resources so Lensix can evaluate Defender-related findings server-side
from an uploaded inventory file.

A third helper, `get_public_ip_addresses()`, lives here too (same
subscription-wide-list-client pattern as the two above) but is NOT called
from `gather()` — it exists for `azure/scanmodules/vm_checks.py` to resolve
the `public_ip_address` *reference* on a NIC's `ip_configurations` (only an
`.id` on a plain `list_all()` result) to an actual address.
"""

from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.security import SecurityCenter
from ._util import resource_group as _resource_group, as_dict as _as_dict


def get_pricings(credential, subscription_id):
    security_client = SecurityCenter(credential, subscription_id)
    return list(security_client.pricings.list())


def get_network_interfaces(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.network_interfaces.list_all())


def get_public_ip_addresses(credential, subscription_id):
    network_client = NetworkManagementClient(credential, subscription_id)
    return list(network_client.public_ip_addresses.list_all())


def gather(credential, subscription_id, writer):
    # Isolated separately — a pricings-list failure must not prevent NIC
    # gathering (and vice versa). A pricings-list failure still yields an
    # empty plan list here rather than aborting the gather, so downstream
    # checks can evaluate against "no plans found" instead of being
    # skipped entirely.
    try:
        pricings = get_pricings(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='defender:pricings', message=e)
        pricings = []

    for pricing in pricings:
        # No tags= here: Pricing (Microsoft Defender for Cloud's own
        # subscription-wide plan tier) has no `tags` field on its own SDK
        # model at all (confirmed — the SDK discards it with a warning if
        # passed), a control-plane object in the same class as
        # authorization's role_definition/securitycenter's own
        # security_contact/security_setting.
        writer.add_resource(
            resource_type='defender_pricing',
            region='global',
            resource_id=pricing.id,
            resource_name=pricing.name,
            raw=_as_dict(pricing),
        )

    try:
        nics = get_network_interfaces(credential, subscription_id)
    except Exception as e:
        writer.add_error(region='global', source='defender:network_interfaces', message=e)
        nics = []

    for nic in nics:
        nic_raw = _as_dict(nic)
        writer.add_resource(
            resource_type='network_interface',
            region=nic.location or 'global',
            resource_id=nic.id,
            resource_name=nic.name,
            scope_id=_resource_group(nic.id),
            raw=nic_raw,
            tags=nic_raw.get('tags'),
        )
