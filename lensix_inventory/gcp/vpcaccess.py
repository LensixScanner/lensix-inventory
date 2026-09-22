"""Serverless VPC Access gathering — one raw record per connector.

Connectors are project+location-scoped: unlike Cloud Run's/Artifact
Registry's own APIs, connectors().list()'s own `parent` parameter does
NOT accept the `locations/-` wildcard (confirmed against the real
discovery document schema — its own pattern requires a specific
`projects/*/locations/*`), so real locations are listed first via
projects.locations.list(), then connectors are listed per location — the
same two-step pattern kms.py's own get_locations()/get_key_rings()
already established.

Public-access/state/throughput evaluation is left server-side (no checks
exist for this resource type yet — this module exists purely to give
cloudrun.py's/functions.py's own VPC-connector references a real,
persisted target resource to point an edge at, same "resources with zero
checks" shape as AWS's own commitments_checks.py/fsx_checks.py).
"""

from googleapiclient import discovery

from . import _util, vpc


def get_locations(vpcaccess, project_id):
    locations = []
    request = vpcaccess.projects().locations().list(name=f'projects/{project_id}')
    while request is not None:
        resp = request.execute()
        locations.extend(resp.get('locations', []))
        request = vpcaccess.projects().locations().list_next(previous_request=request, previous_response=resp)
    return locations


def get_connectors(vpcaccess, parent):
    connectors = []
    request = vpcaccess.projects().locations().connectors().list(parent=parent)
    while request is not None:
        resp = request.execute()
        connectors.extend(resp.get('connectors', []))
        request = vpcaccess.projects().locations().connectors().list_next(previous_request=request, previous_response=resp)
    return connectors


def gather(project_id, credentials, writer):
    vpcaccess = discovery.build('vpcaccess', 'v1', credentials=credentials)
    compute = discovery.build('compute', 'v1', credentials=credentials)

    # Connector.network and Subnet.name are each documented as a bare
    # name, not a URL ("Optional. Name of a VPC network." /
    # "Subnet name (relative, not fully qualified)" — confirmed against
    # the real discovery document schema) — still resolved via vpc.py's
    # own exported maps rather than assumed byte-identical to the target
    # resource's own resource_id, same discipline as every other
    # cross-module network/subnet edge this effort added. An accepted,
    # disclosed duplicate of the same two list calls vpc.py's own
    # gather() already makes elsewhere in the same scan.
    try:
        network_id_by_name = vpc.get_network_selflink_by_name(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='vpc_connector (network resolution)', message=e)
        network_id_by_name = {}
    try:
        subnet_id_by_key = vpc.get_subnet_selflink_by_key(compute, project_id)
    except Exception as e:
        writer.add_error(region='global', source='vpc_connector (subnet resolution)', message=e)
        subnet_id_by_key = {}

    try:
        locations = get_locations(vpcaccess, project_id)
    except Exception as e:
        writer.add_error(region='global', source='vpc_connector', message=e)
        return

    for location in locations:
        location_id = location.get('locationId') or location.get('name', '').split('/')[-1]
        parent = f'projects/{project_id}/locations/{location_id}'
        try:
            connectors = get_connectors(vpcaccess, parent)
        except Exception as e:
            writer.add_error(region=location_id, source='vpc_connector', message=e)
            continue

        for connector in connectors:
            name = connector.get('name', '')
            # No tags= here: Connector has no `labels` field in the
            # Serverless VPC Access API at all — a genuine architectural
            # N/A, same class as kms.py's own KeyRing.
            recorded = writer.add_resource(
                resource_type='vpc_connector',
                region=location_id,
                resource_id=name,
                resource_name=name.split('/')[-1],
                raw=connector,
            )
            if not recorded:
                continue
            network_id = network_id_by_name.get(_util.extract_network_name(connector.get('network')))
            if network_id:
                writer.add_edge(from_type='vpc_connector', from_id=name, to_type='vpc_network', to_id=network_id, relationship='in_vpc_network')
            subnet_name = (connector.get('subnet') or {}).get('name')
            if subnet_name:
                # A cross-project subnet (Subnet.projectId set to a
                # different project) is never in this project's own
                # subnet_id_by_key map, so it correctly produces no edge
                # rather than a wrong one.
                subnet_id = subnet_id_by_key.get((location_id, subnet_name))
                if subnet_id:
                    writer.add_edge(from_type='vpc_connector', from_id=name, to_type='subnet', to_id=subnet_id, relationship='in_subnet')
