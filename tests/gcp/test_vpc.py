"""Unit tests for vpc.py — firewall rules, VPC networks, and subnets.

No tags= wiring in this module at all: none of Firewall, Network, or
Subnetwork have a `labels` field in the Compute Engine v1 API, confirmed
against the real discovery document schema — a genuine architectural N/A,
same class as kms.py's own KeyRing. This covers gather()'s own
resource-shape wiring instead.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.gcp.vpc as m


def _rule(*, name='allow-ssh', self_link='https://compute.../firewalls/allow-ssh'):
    return {'name': name, 'selfLink': self_link}


def _network(*, name='default', self_link='https://compute.../networks/default'):
    return {'name': name, 'selfLink': self_link}


def _subnet(*, name='sub1', self_link='https://compute.../subnetworks/sub1'):
    return {'name': name, 'selfLink': self_link}


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
        assert resource_types == ['firewall_rule', 'vpc_network', 'subnet']
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
