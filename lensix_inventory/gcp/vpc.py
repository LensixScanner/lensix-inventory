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


def get_network_selflink_by_name(compute, project_id):
    """name -> selfLink map for every network in this project. Exported for
    OTHER gather modules (compute.py, gke.py, sql.py, ...) that need to
    resolve one of their own resource's `network` reference fields to the
    actual persisted vpc_network resource_id — see this module's own
    gather() comment on network_id_by_name for why the raw reference
    string can't just be used as an edge's to_id directly. Reuses
    get_networks() rather than a second, separate fetch."""
    return {n.get('name', ''): n.get('selfLink', n.get('name', '')) for n in get_networks(compute, project_id)}


def get_subnet_selflink_by_key(compute, project_id):
    """(region, name) -> selfLink map for every subnet in this project —
    keyed by region too since a subnetwork's own name is only unique
    within its region, not project-wide. Same exporting rationale as
    get_network_selflink_by_name. Reuses get_subnets() rather than a
    second, separate fetch."""
    return {(region, s.get('name', '')): s.get('selfLink', s.get('name', '')) for region, s in get_subnets(compute, project_id)}


def gather(project_id, credentials, writer):
    # No tags= anywhere in this module: none of Firewall, Network, or
    # Subnetwork have a `labels` field in the Compute Engine v1 API at
    # all — confirmed against the real discovery document schema, not
    # assumed (unlike most GCE resource types — Instance/Disk/Image/
    # Address among others — which do support labels). A genuine
    # architectural N/A, same class as kms.py's own KeyRing.
    compute = discovery.build('compute', 'v1', credentials=credentials)

    # Networks are gathered FIRST (not in their original position after
    # firewall rules) so this map is ready before firewall_rule/subnet
    # need it below. A firewall_rule/subnet's own `network` reference field
    # is a URL that's merely DOCUMENTED as accepting a full, partial, or
    # bare-name form (confirmed against the real discovery document
    # schema) — it is NOT guaranteed to come back byte-identical to the
    # referenced Network's own `selfLink` (used as vpc_network's
    # resource_id), so a firewall_rule -> vpc_network edge can't safely
    # just reuse the raw reference string as its to_id the way AWS's
    # VpcId-based resources can (there, the id embedded in every other
    # resource's own fields already IS the VPC's own resource_id, no
    # separate synthesized selfLink involved). Resolving through each
    # network's own short NAME instead — unique per project, so this join
    # is always exact — guarantees the edge's to_id always matches a real,
    # persisted vpc_network's resource_id, or is skipped rather than risk
    # emitting a dangling edge into a diagram that resolves nodes by exact
    # resource_id match.
    network_id_by_name = {}
    try:
        for network in get_networks(compute, project_id):
            name = network.get('name', '')
            resource_id = network.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='vpc_network',
                region='global',
                resource_id=resource_id,
                resource_name=name,
                scope_id=name,
                raw=network,
            )
            if recorded:
                network_id_by_name[name] = resource_id
    except Exception as e:
        writer.add_error(region='global', source='vpc_network', message=e)

    try:
        for rule in get_firewall_rules(compute, project_id):
            name = rule.get('name', '')
            rule_id = rule.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='firewall_rule',
                region='global',
                resource_id=rule_id,
                resource_name=name,
                scope_id=_util.extract_network_name(rule.get('network')),
                raw=rule,
            )
            if not recorded:
                continue
            network_id = network_id_by_name.get(_util.extract_network_name(rule.get('network')))
            if network_id:
                writer.add_edge(from_type='firewall_rule', from_id=rule_id, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
    except Exception as e:
        writer.add_error(region='global', source='firewall_rule', message=e)

    try:
        for region, subnet in get_subnets(compute, project_id):
            name = subnet.get('name', '')
            subnet_id = subnet.get('selfLink', name)
            recorded = writer.add_resource(
                resource_type='subnet',
                region=region,
                resource_id=subnet_id,
                resource_name=name,
                scope_id=_util.extract_network_name(subnet.get('network')),
                raw=subnet,
            )
            if not recorded:
                continue
            network_id = network_id_by_name.get(_util.extract_network_name(subnet.get('network')))
            if network_id:
                writer.add_edge(from_type='subnet', from_id=subnet_id, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
    except Exception as e:
        writer.add_error(region='global', source='subnet', message=e)
