"""Unit tests for vpc.py — firewall rules, VPC networks, and subnets.

No tags= wiring in this module at all: none of Firewall, Network, or
Subnetwork have a `labels` field in the Compute Engine v1 API, confirmed
against the real discovery document schema — a genuine architectural N/A,
same class as kms.py's own KeyRing. This covers gather()'s own
resource-shape wiring instead.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.vpc as m


def _rule(*, name='allow-ssh', self_link='https://compute.../firewalls/allow-ssh', network=None):
    rule = {'name': name, 'selfLink': self_link}
    if network is not None:
        rule['network'] = network
    return rule


def _network(*, name='default', self_link='https://compute.../networks/default'):
    return {'name': name, 'selfLink': self_link}


def _subnet(*, name='sub1', self_link='https://compute.../subnetworks/sub1', network=None):
    subnet = {'name': name, 'selfLink': self_link}
    if network is not None:
        subnet['network'] = network
    return subnet


def _compute_client(rules=None, networks=None, subnets_by_region=None):
    compute = MagicMock()

    fw_req = MagicMock()
    fw_req.execute.return_value = {'items': rules or []}
    compute.firewalls.return_value.list.return_value = fw_req
    compute.firewalls.return_value.list_next.return_value = None

    net_req = MagicMock()
    net_req.execute.return_value = {'items': networks or []}
    compute.networks.return_value.list.return_value = net_req
    compute.networks.return_value.list_next.return_value = None

    subnets_by_region = subnets_by_region or {}
    sub_req = MagicMock()
    sub_req.execute.return_value = {
        'items': {f'regions/{region}': {'subnetworks': subs} for region, subs in subnets_by_region.items()}
    }
    compute.subnetworks.return_value.aggregatedList.return_value = sub_req
    compute.subnetworks.return_value.aggregatedList_next.return_value = None
    return compute


class TestGetNetworkSelflinkByName:
    def test_maps_each_network_name_to_its_selflink(self):
        n1 = _network(name='default', self_link='https://compute.../networks/default')
        n2 = _network(name='prod', self_link='https://compute.../networks/prod')
        compute = _compute_client(networks=[n1, n2])
        assert m.get_network_selflink_by_name(compute, 'p') == {
            'default': n1['selfLink'], 'prod': n2['selfLink'],
        }

    def test_no_networks_returns_an_empty_map(self):
        compute = _compute_client()
        assert m.get_network_selflink_by_name(compute, 'p') == {}


class TestGetSubnetSelflinkByKey:
    def test_maps_each_region_name_pair_to_its_selflink(self):
        s1 = _subnet(name='sub1', self_link='https://compute.../regions/us-central1/subnetworks/sub1')
        s2 = _subnet(name='sub1', self_link='https://compute.../regions/europe-west1/subnetworks/sub1')
        compute = _compute_client(subnets_by_region={'us-central1': [s1], 'europe-west1': [s2]})
        assert m.get_subnet_selflink_by_key(compute, 'p') == {
            ('us-central1', 'sub1'): s1['selfLink'],
            ('europe-west1', 'sub1'): s2['selfLink'],
        }

    def test_no_subnets_returns_an_empty_map(self):
        compute = _compute_client()
        assert m.get_subnet_selflink_by_key(compute, 'p') == {}


class TestGather:
    def test_adds_one_resource_per_type(self):
        rule = _rule()
        network = _network()
        subnet = _subnet()
        compute = _compute_client(rules=[rule], networks=[network], subnets_by_region={'us-central1': [subnet]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        assert writer.add_resource.call_count == 3
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert resource_types == ['vpc_network', 'firewall_rule', 'subnet']
        for call in writer.add_resource.call_args_list:
            assert 'tags' not in call.kwargs

    def test_a_firewall_rules_failure_does_not_prevent_the_other_types(self):
        compute = _compute_client(networks=[_network()])
        compute.firewalls.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        assert any(c.kwargs['source'] == 'firewall_rule' for c in writer.add_error.call_args_list)
        resource_types = [c.kwargs['resource_type'] for c in writer.add_resource.call_args_list]
        assert resource_types == ['vpc_network']

    def test_nothing_adds_nothing(self):
        compute = _compute_client()
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_firewall_rule_produces_a_vpc_network_edge_via_full_selflink_reference(self):
        network = _network(name='default', self_link='https://compute.../networks/default')
        rule = _rule(network='https://compute.../networks/default')
        compute = _compute_client(rules=[rule], networks=[network])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='firewall_rule', from_id=rule['selfLink'],
            to_type='vpc_network', to_id=network['selfLink'], relationship='in_vpc_network',
        )

    def test_a_firewall_rule_referencing_a_network_by_partial_url_still_resolves_correctly(self):
        # The real, documented GCP quirk this whole edge-resolution design
        # exists for: Firewall.network is allowed to come back as a
        # partial URL/bare name (confirmed against the real discovery
        # document), which would NOT string-match the target vpc_network's
        # own full-selfLink resource_id if used directly as the edge's
        # to_id. Resolving via the network's own short NAME instead must
        # still land on the correct, full-selfLink resource_id.
        network = _network(name='default', self_link='https://compute.../networks/default')
        rule = _rule(network='projects/p/global/networks/default')
        compute = _compute_client(rules=[rule], networks=[network])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='firewall_rule', from_id=rule['selfLink'],
            to_type='vpc_network', to_id=network['selfLink'], relationship='in_vpc_network',
        )

    def test_a_subnet_produces_a_vpc_network_edge(self):
        network = _network(name='default', self_link='https://compute.../networks/default')
        subnet = _subnet(network='https://compute.../networks/default')
        compute = _compute_client(networks=[network], subnets_by_region={'us-central1': [subnet]})
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_called_once_with(
            from_type='subnet', from_id=subnet['selfLink'],
            to_type='vpc_network', to_id=network['selfLink'], relationship='in_vpc_network',
        )

    def test_a_firewall_rule_referencing_an_unresolvable_network_produces_no_edge(self):
        # e.g. the referenced network's own fetch failed, or it's a
        # cross-project/peered network this project's own networks().list()
        # would never return -- degrade to no edge rather than a dangling one.
        rule = _rule(network='https://compute.../networks/unknown-network')
        compute = _compute_client(rules=[rule], networks=[_network()])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_firewall_rule_with_no_network_field_produces_no_edge(self):
        rule = _rule()
        assert 'network' not in rule
        compute = _compute_client(rules=[rule], networks=[_network()])
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_networks_fetch_failure_leaves_the_resolution_map_empty_and_produces_no_edges(self):
        rule = _rule(network='https://compute.../networks/default')
        compute = _compute_client(rules=[rule])
        compute.networks.return_value.list.side_effect = RuntimeError('boom')
        writer = MagicMock()
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()

    def test_a_fully_suppressed_network_is_not_added_to_the_resolution_map(self):
        network = _network(name='default', self_link='https://compute.../networks/default')
        rule = _rule(network='https://compute.../networks/default')
        compute = _compute_client(rules=[rule], networks=[network])
        writer = MagicMock()
        writer.add_resource.side_effect = lambda **kw: kw['resource_type'] != 'vpc_network'
        with patch.object(m.discovery, 'build', return_value=compute):
            m.gather('p', MagicMock(), writer)
        writer.add_edge.assert_not_called()
