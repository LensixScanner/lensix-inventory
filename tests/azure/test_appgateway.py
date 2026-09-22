"""Unit tests for lensix_inventory.azure.appgateway — Application Gateways."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.appgateway as m


def _gateway(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/applicationGateways/gw1', name='gw1',
             gw_subnet_ids=None, backend_pools=None):
    gw = MagicMock()
    gw.location = location
    gw.id = rid
    gw.name = name
    raw = {'id': rid, 'name': name}
    if gw_subnet_ids is not None:
        raw['gateway_ip_configurations'] = [{'subnet': {'id': sid}} for sid in gw_subnet_ids]
    if backend_pools is not None:
        raw['backend_address_pools'] = backend_pools
    gw.as_dict.return_value = raw
    return gw


class TestGather:
    def test_adds_one_resource_per_gateway(self):
        w = MagicMock()
        gw = _gateway()
        client = MagicMock()
        client.application_gateways.list_all.return_value = [gw]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='application_gateway', region='eastus', resource_id=gw.id,
            resource_name='gw1', scope_id='my-rg', raw={'id': gw.id, 'name': 'gw1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        gw = _gateway()
        gw.as_dict.return_value = {'id': gw.id, 'name': 'gw1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.application_gateways.list_all.return_value = [gw]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        gw = _gateway(location=None)
        client = MagicMock()
        client.application_gateways.list_all.return_value = [gw]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_no_gateways_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.application_gateways.list_all.return_value = []
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, gw, w=None):
        w = w or MagicMock()
        client = MagicMock()
        client.application_gateways.list_all.return_value = [gw]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        return w

    def test_a_gateway_subnet_gets_an_in_subnet_edge(self):
        gw = _gateway(gw_subnet_ids=['subnet-1'])
        w = self._gather(gw)
        w.add_edge.assert_called_once_with(
            from_type='application_gateway', from_id=gw.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_a_backend_pools_nic_ip_config_gets_a_routes_to_nic_edge_stripped_to_the_parent_nic(self):
        gw = _gateway(backend_pools=[{'name': 'pool1', 'backend_ip_configurations': [
            {'id': '/subscriptions/s1/.../networkInterfaces/nic1/ipConfigurations/ipconfig1'},
        ]}])
        w = self._gather(gw)
        w.add_edge.assert_called_once_with(
            from_type='application_gateway', from_id=gw.id, to_type='network_interface',
            to_id='/subscriptions/s1/.../networkInterfaces/nic1', relationship='routes_to_nic',
        )

    def test_backend_addresses_alone_produce_no_edge(self):
        # backend_addresses (FQDN/plain-IP targets) have no persisted
        # resource to join to — only backend_ip_configurations counts.
        gw = _gateway(backend_pools=[{'name': 'pool1', 'backend_addresses': [{'fqdn': 'example.com'}]}])
        w = self._gather(gw)
        w.add_edge.assert_not_called()

    def test_no_subnets_or_backend_pools_gets_no_edges(self):
        gw = _gateway()
        w = self._gather(gw)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_gateway_gets_no_edges(self):
        gw = _gateway(gw_subnet_ids=['subnet-1'])
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(gw, w=w)
        w.add_edge.assert_not_called()
