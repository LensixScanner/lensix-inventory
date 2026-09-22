"""Unit tests for lensix_inventory.azure.lb — Load Balancers.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.lb as m


def _lb(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/loadBalancers/lb1', name='lb1',
        frontend_subnet_ids=None, backend_pools=None):
    lb = MagicMock()
    lb.location = location
    lb.id = rid
    lb.name = name
    raw = {'id': rid, 'name': name}
    if frontend_subnet_ids is not None:
        raw['frontend_ip_configurations'] = [{'subnet': {'id': sid}} for sid in frontend_subnet_ids]
    if backend_pools is not None:
        raw['backend_address_pools'] = backend_pools
    lb.as_dict.return_value = raw
    return lb


def _backend_pool(*, vnet_id=None, nic_ip_config_ids=None):
    pool = {}
    if vnet_id is not None:
        pool['virtual_network'] = {'id': vnet_id}
    if nic_ip_config_ids is not None:
        pool['backend_ip_configurations'] = [{'id': cid} for cid in nic_ip_config_ids]
    return pool


class TestGather:
    def test_adds_one_resource_per_lb(self):
        w = MagicMock()
        lb = _lb()
        network = MagicMock()
        network.load_balancers.list_all.return_value = [lb]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='load_balancer', region='eastus', resource_id=lb.id,
            resource_name='lb1', scope_id='my-rg',
            raw={'id': lb.id, 'name': 'lb1', '_DiagnosticSettings': []},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        lb = _lb()
        lb.as_dict.return_value = {'id': lb.id, 'name': 'lb1', 'tags': {'lensix-suppress': 'true'}}
        network = MagicMock()
        network.load_balancers.list_all.return_value = [lb]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        network = MagicMock()
        network.load_balancers.list_all.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'lb:load_balancers'
        w.add_resource.assert_not_called()

    def test_no_lbs_gathers_nothing(self):
        w = MagicMock()
        network = MagicMock()
        network.load_balancers.list_all.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, lb, w=None):
        w = w or MagicMock()
        network = MagicMock()
        network.load_balancers.list_all.return_value = [lb]
        monitor = MagicMock()
        monitor.diagnostic_settings.list.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network), \
             patch('azure.mgmt.monitor.MonitorManagementClient', return_value=monitor):
            m.gather('cred', 'sub-1', w)
        return w

    def test_an_internal_frontend_gets_an_in_subnet_edge(self):
        lb = _lb(frontend_subnet_ids=['subnet-1'])
        w = self._gather(lb)
        w.add_edge.assert_called_once_with(
            from_type='load_balancer', from_id=lb.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_a_backend_pools_vnet_gets_an_in_vnet_edge(self):
        lb = _lb(backend_pools=[_backend_pool(vnet_id='vnet-1')])
        w = self._gather(lb)
        w.add_edge.assert_called_once_with(
            from_type='load_balancer', from_id=lb.id, to_type='virtual_network', to_id='vnet-1', relationship='in_vnet',
        )

    def test_a_backend_pools_nic_ip_config_gets_a_routes_to_nic_edge_stripped_to_the_parent_nic(self):
        lb = _lb(backend_pools=[_backend_pool(
            nic_ip_config_ids=['/subscriptions/s1/.../networkInterfaces/nic1/ipConfigurations/ipconfig1'],
        )])
        w = self._gather(lb)
        w.add_edge.assert_called_once_with(
            from_type='load_balancer', from_id=lb.id, to_type='network_interface',
            to_id='/subscriptions/s1/.../networkInterfaces/nic1', relationship='routes_to_nic',
        )

    def test_multiple_backend_members_across_pools_each_get_their_own_edge(self):
        lb = _lb(backend_pools=[
            _backend_pool(nic_ip_config_ids=['/subscriptions/s1/.../networkInterfaces/nic1/ipConfigurations/ipconfig1']),
            _backend_pool(nic_ip_config_ids=['/subscriptions/s1/.../networkInterfaces/nic2/ipConfigurations/ipconfig1']),
        ])
        w = self._gather(lb)
        to_ids = {c.kwargs['to_id'] for c in w.add_edge.call_args_list}
        assert to_ids == {
            '/subscriptions/s1/.../networkInterfaces/nic1',
            '/subscriptions/s1/.../networkInterfaces/nic2',
        }

    def test_no_frontends_or_backend_pools_gets_no_edges(self):
        lb = _lb()
        w = self._gather(lb)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_lb_gets_no_edges(self):
        lb = _lb(frontend_subnet_ids=['subnet-1'], backend_pools=[_backend_pool(vnet_id='vnet-1')])
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(lb, w=w)
        w.add_edge.assert_not_called()
