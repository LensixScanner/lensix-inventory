"""Unit tests for vpcaccess.py — Serverless VPC Access connectors.

No `labels` field exists on Connector in the Serverless VPC Access API at
all — confirmed against the real discovery document schema — a genuine
architectural N/A, same class as kms.py's own KeyRing.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.vpcaccess as m


def _location(location_id='us-central1', name=None):
    return {'name': name or f'projects/p/locations/{location_id}', 'locationId': location_id}


def _connector(*, name='projects/p/locations/us-central1/connectors/my-connector',
                network=None, subnet_name=None, subnet_project=None):
    c = {'name': name}
    if network is not None:
        c['network'] = network
    if subnet_name is not None:
        c['subnet'] = {'name': subnet_name}
        if subnet_project is not None:
            c['subnet']['projectId'] = subnet_project
    return c


def _network(*, name='default', self_link='https://compute.../networks/default'):
    return {'name': name, 'selfLink': self_link}


def _subnet(*, name='sub1', self_link='https://compute.../regions/us-central1/subnetworks/sub1'):
    return {'name': name, 'selfLink': self_link}


def _client(locations, connectors_by_parent=None, networks=None, subnets_by_region=None):
    vpcaccess = MagicMock()

    loc_req = MagicMock()
    loc_req.execute.return_value = {'locations': locations}
    vpcaccess.projects.return_value.locations.return_value.list.return_value = loc_req
    vpcaccess.projects.return_value.locations.return_value.list_next.return_value = None

    connectors_by_parent = connectors_by_parent or {}

    def _conn_list(parent):
        r = MagicMock()
        r.execute.return_value = {'connectors': connectors_by_parent.get(parent, [])}
        return r
    vpcaccess.projects.return_value.locations.return_value.connectors.return_value.list.side_effect = _conn_list
    vpcaccess.projects.return_value.locations.return_value.connectors.return_value.list_next.return_value = None

    # gather() also resolves each connector's network/subnet references
    # via vpc.py's own exported maps, through a SEPARATE discovery.build()
    # call that this same patched mock also answers — terminated
    # immediately (empty results) so tests not exercising those edges
    # don't need to know about this, and so the real pagination loop
    # doesn't spin forever against an unconfigured MagicMock's own
    # always-truthy list_next()/aggregatedList_next() return.
    vpcaccess.networks.return_value.list.return_value.execute.return_value = {'items': networks or []}
    vpcaccess.networks.return_value.list_next.return_value = None
    subnets_by_region = subnets_by_region or {}
    vpcaccess.subnetworks.return_value.aggregatedList.return_value.execute.return_value = {
        'items': {f'regions/{r}': {'subnetworks': subs} for r, subs in subnets_by_region.items()}}
    vpcaccess.subnetworks.return_value.aggregatedList_next.return_value = None

    return vpcaccess


class TestGather:
    def test_adds_one_resource_per_connector(self):
        loc = _location()
        conn = _connector()
        client = _client([loc], {f'projects/p/locations/us-central1': [conn]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_called_once()
        kwargs = writer.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'vpc_connector'
        assert kwargs['region'] == 'us-central1'
        assert kwargs['resource_id'] == conn['name']
        assert kwargs['resource_name'] == 'my-connector'
        assert 'tags' not in kwargs

    def test_a_locations_list_failure_is_isolated_and_gather_returns_without_raising(self):
        client = _client([])
        client.projects.return_value.locations.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_error.assert_called_once()
        writer.add_resource.assert_not_called()

    def test_a_connectors_list_failure_for_one_location_does_not_abort_the_others(self):
        bad = _location(location_id='us-east1')
        good = _location(location_id='us-central1')
        conn = _connector(name='projects/p/locations/us-central1/connectors/good-conn')
        client = _client([bad, good], {'projects/p/locations/us-central1': [conn]})

        def _conn_list(parent):
            if 'us-east1' in parent:
                raise RuntimeError('boom')
            r = MagicMock()
            r.execute.return_value = {'connectors': [conn]}
            return r
        client.projects.return_value.locations.return_value.connectors.return_value.list.side_effect = _conn_list

        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'vpc_connector' and c.kwargs['region'] == 'us-east1' for c in writer.add_error.call_args_list)
        writer.add_resource.assert_called_once()

    def test_no_locations_adds_nothing(self):
        client = _client([])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_connector_produces_a_vpc_network_edge(self):
        loc = _location()
        network = _network()
        conn = _connector(network='default')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]}, networks=[network])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        edges = [c.kwargs for c in writer.add_edge.call_args_list]
        assert {'from_type': 'vpc_connector', 'from_id': conn['name'], 'to_type': 'vpc_network', 'to_id': network['selfLink'], 'relationship': 'in_vpc_network'} in edges

    def test_a_connector_produces_a_subnet_edge(self):
        loc = _location()
        subnet = _subnet()
        conn = _connector(subnet_name='sub1')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]}, subnets_by_region={'us-central1': [subnet]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        edges = [c.kwargs for c in writer.add_edge.call_args_list]
        assert {'from_type': 'vpc_connector', 'from_id': conn['name'], 'to_type': 'subnet', 'to_id': subnet['selfLink'], 'relationship': 'in_subnet'} in edges

    def test_a_cross_project_subnet_produces_no_edge(self):
        loc = _location()
        conn = _connector(subnet_name='sub1', subnet_project='other-project')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_connector_with_no_network_or_subnet_produces_no_edges(self):
        loc = _location()
        conn = _connector()
        client = _client([loc], {'projects/p/locations/us-central1': [conn]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_an_unresolvable_network_produces_no_edge(self):
        loc = _location()
        conn = _connector(network='unknown-network')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]}, networks=[_network()])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_network_resolution_failure_produces_no_vpc_network_edge(self):
        loc = _location()
        conn = _connector(network='default')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]})
        client.networks.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'vpc_connector (network resolution)' for c in writer.add_error.call_args_list)
        writer.add_edge.assert_not_called()
        writer.add_resource.assert_called_once()

    def test_a_fully_suppressed_connector_produces_no_edges(self):
        # Connector has no tags/labels concept at all (see module
        # docstring), so full suppression can never actually trigger here
        # in practice -- exercised defensively anyway, same discipline as
        # every other migrated module this effort.
        loc = _location()
        network = _network()
        conn = _connector(network='default')
        client = _client([loc], {'projects/p/locations/us-central1': [conn]}, networks=[network])
        writer = MagicMock()
        writer.add_resource.return_value = False
        with patch.object(m.discovery, 'build', return_value=client):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()
