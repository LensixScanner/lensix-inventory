"""VPC networking gathering — firewall rules, VPC networks, and subnets.

Per-rule port evaluation (open SSH/RDP/MySQL/.../all-ports from 0.0.0.0/0
or ::/0) is all pure functions of one firewall rule dict — left
server-side, since it's evaluation over the raw rule, not gathering. Same
for public-egress, missing-firewall-logging, metadata-logging, and port-
range-rule evaluation (all read fields already present on the rule dict)
and default-network/missing-flow-logs/missing-private-google-access
evaluation (read fields already present on the network/subnet dict).

`vpc_network` and `subnet` are gathered as their own resource records too
(not just firewall rules), mirroring aws/vpc.py's approach of treating
every resource a list call is already making available as also worth
persisting. Without uploading networks/subnets themselves, Lensix would
have no raw data to re-evaluate default-network/missing-flow-logs/missing-
private-google-access findings from after import.
"""

from googleapiclient import discovery

from . import _util


def get_firewall_rules(compute, project_id):
    rules = []
    request = compute.firewalls().list(project=project_id)
    while request is not None:
        resp = request.execute()
        rules.extend(resp.get('items', []))
        request = compute.firewalls().list_next(previous_request=request, previous_response=resp)
    return rules


def get_networks(compute, project_id):
    networks = []
    request = compute.networks().list(project=project_id)
    while request is not None:
        resp = request.execute()
        networks.extend(resp.get('items', []))
        request = compute.networks().list_next(previous_request=request, previous_response=resp)
    return networks


def get_subnets(compute, project_id):
    """All subnets across all regions, as (region, subnet) tuples."""
    subnets = []
    request = compute.subnetworks().aggregatedList(project=project_id)
    while request is not None:
        resp = request.execute()
        for region_key, data in resp.get('items', {}).items():
            region = region_key.rsplit('/', 1)[-1] if '/' in region_key else region_key
            for subnet in data.get('subnetworks', []):
                subnets.append((region, subnet))
        request = compute.subnetworks().aggregatedList_next(previous_request=request, previous_response=resp)
    return subnets


def gather(project_id, credentials, writer):
    # No tags= anywhere in this module: none of Firewall, Network, or
    # Subnetwork have a `labels` field in the Compute Engine v1 API at
    # all — confirmed against the real discovery document schema, not
    # assumed (unlike most GCE resource types — Instance/Disk/Image/
    # Address among others — which do support labels). A genuine
    # architectural N/A, same class as kms.py's own KeyRing.
    compute = discovery.build('compute', 'v1', credentials=credentials)

    try:
        for rule in get_firewall_rules(compute, project_id):
            name = rule.get('name', '')
            writer.add_resource(
                resource_type='firewall_rule',
                region='global',
                resource_id=rule.get('selfLink', name),
                resource_name=name,
                scope_id=_util.extract_network_name(rule.get('network')),
                raw=rule,
            )
    except Exception as e:
        writer.add_error(region='global', source='firewall_rule', message=e)

    try:
        for network in get_networks(compute, project_id):
            name = network.get('name', '')
            writer.add_resource(
                resource_type='vpc_network',
                region='global',
                resource_id=network.get('selfLink', name),
                resource_name=name,
                scope_id=name,
                raw=network,
            )
    except Exception as e:
        writer.add_error(region='global', source='vpc_network', message=e)

    try:
        for region, subnet in get_subnets(compute, project_id):
            name = subnet.get('name', '')
            writer.add_resource(
                resource_type='subnet',
                region=region,
                resource_id=subnet.get('selfLink', name),
                resource_name=name,
                scope_id=_util.extract_network_name(subnet.get('network')),
                raw=subnet,
            )
    except Exception as e:
        writer.add_error(region='global', source='subnet', message=e)
