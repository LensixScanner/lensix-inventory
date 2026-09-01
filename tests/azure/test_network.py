"""Unit tests for lensix_inventory.azure.network — VNets and their peering
connections.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring, including the
one genuinely novel case in this rollout: VirtualNetworkPeering has no
`tags` field of its own (the SDK model rejects it as a constructor kwarg),
so each peering inherits the PARENT VNet's own tags at gather time instead
of getting its own tag lookup.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.network as m


def _vnet(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/vnet1',
          name='vnet1', tags=None, subnets=None):
    vnet = MagicMock()
    vnet.location = location
    vnet.id = rid
    vnet.name = name
    vnet.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags, 'subnets': subnets}
    return vnet


def _subnet(sid='/subscriptions/s1/.../virtualNetworks/vnet1/subnets/subnet1', name='subnet1', address_prefix='10.0.1.0/24'):
    return {'id': sid, 'name': name, 'address_prefix': address_prefix}


def _peering(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/virtualNetworks/vnet1/virtualNetworkPeerings/peer1',
             name='peer1'):
    peering = MagicMock()
    peering.id = rid
    peering.name = name
    peering.as_dict.return_value = {'id': rid, 'name': name}
    return peering


class TestGather:
    def test_adds_one_resource_per_vnet_and_peering(self):
        w = MagicMock()
        vnet = _vnet()
        peering = _peering()
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = [peering]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 2
        vnet_call, peering_call = w.add_resource.call_args_list
        assert vnet_call.kwargs['resource_type'] == 'virtual_network'
        assert vnet_call.kwargs['resource_id'] == vnet.id
        assert vnet_call.kwargs['tags'] is None
        assert peering_call.kwargs['resource_type'] == 'vnet_peering'
        assert peering_call.kwargs['resource_id'] == peering.id
        assert peering_call.kwargs['scope_id'] == 'my-rg'

    def test_peering_inherits_the_parent_vnets_own_tags(self):
        w = MagicMock()
        vnet = _vnet(tags={'lensix-suppress-checks': 'network_unknownpeering'})
        peering = _peering()
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = [peering]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        vnet_call, peering_call = w.add_resource.call_args_list
        assert vnet_call.kwargs['tags'] == {'lensix-suppress-checks': 'network_unknownpeering'}
        assert peering_call.kwargs['tags'] == {'lensix-suppress-checks': 'network_unknownpeering'}

    def test_fully_suppressing_the_vnet_leaves_its_peering_tagged_the_same_way(self):
        # add_resource() itself is what skips recording a fully-suppressed
        # resource — gather() just needs to pass the same tags through to
        # both calls, which this asserts directly (add_resource's own
        # full-suppress behavior is covered by common/output.py's tests).
        w = MagicMock()
        vnet = _vnet(tags={'lensix-suppress': 'true'})
        peering = _peering()
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = [peering]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        vnet_call, peering_call = w.add_resource.call_args_list
        assert vnet_call.kwargs['tags'] == {'lensix-suppress': 'true'}
        assert peering_call.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_vnet_list_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        network = MagicMock()
        network.virtual_networks.list_all.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'network:virtual_networks'
        w.add_resource.assert_not_called()

    def test_peering_list_failure_for_one_vnet_does_not_abort_the_others(self):
        w = MagicMock()
        bad = _vnet(rid='.../virtualNetworks/bad', name='bad')
        good = _vnet(rid='.../virtualNetworks/good', name='good')
        good_peering = _peering()
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [bad, good]

        def _list(rg, name):
            if name == 'bad':
                raise RuntimeError('boom')
            return [good_peering]
        network.virtual_network_peerings.list.side_effect = _list
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'network:peerings:bad'
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['virtual_network', 'virtual_network', 'vnet_peering']

    def test_no_vnets_gathers_nothing(self):
        w = MagicMock()
        network = MagicMock()
        network.virtual_networks.list_all.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()

    def test_gathers_one_resource_per_subnet_on_the_vnet(self):
        w = MagicMock()
        vnet = _vnet(subnets=[_subnet(sid='sub-a'), _subnet(sid='sub-b', name='subnet2', address_prefix='10.0.2.0/24')])
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['virtual_network', 'subnet', 'subnet']

    def test_subnet_scope_id_is_the_parent_vnets_own_resource_id(self):
        # Mirrors aws/vpc.py's subnet scope_id = VpcId convention — lets the
        # web app's existing scope self-join resolve a subnet's parent
        # network the same way for every provider.
        w = MagicMock()
        vnet = _vnet(rid='vnet-1', subnets=[_subnet()])
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        _, subnet_call = w.add_resource.call_args_list
        assert subnet_call.kwargs['scope_id'] == 'vnet-1'

    def test_subnet_carries_its_own_id_name_and_raw_address_prefix(self):
        w = MagicMock()
        vnet = _vnet(subnets=[_subnet(sid='sub-a', name='subnet1', address_prefix='10.0.1.0/24')])
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        _, subnet_call = w.add_resource.call_args_list
        assert subnet_call.kwargs['resource_id'] == 'sub-a'
        assert subnet_call.kwargs['resource_name'] == 'subnet1'
        assert subnet_call.kwargs['raw']['address_prefix'] == '10.0.1.0/24'

    def test_subnet_inherits_the_parent_vnets_own_tags(self):
        w = MagicMock()
        vnet = _vnet(tags={'lensix-suppress-checks': 'network_unknownpeering'}, subnets=[_subnet()])
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        _, subnet_call = w.add_resource.call_args_list
        assert subnet_call.kwargs['tags'] == {'lensix-suppress-checks': 'network_unknownpeering'}

    def test_a_vnet_with_no_subnets_key_at_all_gathers_no_subnets(self):
        # as_dict() omits 'subnets' entirely for some SDK/API edge cases —
        # confirms the `or []` fallback, not just the `subnets=None` default
        # every other test in this file already implies.
        w = MagicMock()
        vnet = MagicMock()
        vnet.location = 'eastus'
        vnet.id = 'vnet-1'
        vnet.name = 'vnet1'
        vnet.as_dict.return_value = {'id': 'vnet-1', 'name': 'vnet1'}
        network = MagicMock()
        network.virtual_networks.list_all.return_value = [vnet]
        network.virtual_network_peerings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['virtual_network']
