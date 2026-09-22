"""Unit tests for lensix_inventory.azure.networkwatcher — watchers and
their NSG flow logs.

No test file existed for this module before tag-based suppression support
was added — this covers gather()'s own resource/tags wiring. Unlike
azure.network's vnet_peering, FlowLog is a genuinely independently-taggable
ARM resource (confirmed via the SDK model accepting a `tags` kwarg), so
both resource types get their own `raw.get('tags')` — no cascading needed.
"""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.networkwatcher as m


def _watcher(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/networkWatchers/nw1',
             name='nw1', tags=None):
    watcher = MagicMock()
    watcher.location = location
    watcher.id = rid
    watcher.name = name
    watcher.as_dict.return_value = {'id': rid, 'name': name, 'tags': tags}
    return watcher


def _flow_log(rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/networkWatchers/nw1/flowLogs/fl1',
              name='fl1', tags=None, storage_id=None, target_resource_id=None):
    fl = MagicMock()
    fl.id = rid
    fl.name = name
    raw = {'id': rid, 'name': name, 'tags': tags}
    if storage_id is not None:
        raw['storage_id'] = storage_id
    if target_resource_id is not None:
        raw['target_resource_id'] = target_resource_id
    fl.as_dict.return_value = raw
    return fl


class TestGather:
    def test_adds_one_resource_per_watcher_and_flow_log(self):
        w = MagicMock()
        watcher = _watcher()
        fl = _flow_log()
        network = MagicMock()
        network.network_watchers.list_all.return_value = [watcher]
        network.flow_logs.list.return_value = [fl]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_count == 2
        watcher_call, fl_call = w.add_resource.call_args_list
        assert watcher_call.kwargs['resource_type'] == 'network_watcher'
        assert watcher_call.kwargs['tags'] is None
        assert fl_call.kwargs['resource_type'] == 'flow_log'
        assert fl_call.kwargs['tags'] is None

    def test_tags_are_passed_through_independently_for_each_resource_type(self):
        w = MagicMock()
        watcher = _watcher(tags={'lensix-suppress': 'true'})
        fl = _flow_log(tags={'lensix-suppress-checks': 'networkwatcher_lowflowlogretention'})
        network = MagicMock()
        network.network_watchers.list_all.return_value = [watcher]
        network.flow_logs.list.return_value = [fl]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        watcher_call, fl_call = w.add_resource.call_args_list
        assert watcher_call.kwargs['tags'] == {'lensix-suppress': 'true'}
        assert fl_call.kwargs['tags'] == {'lensix-suppress-checks': 'networkwatcher_lowflowlogretention'}

    def test_watcher_list_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        network = MagicMock()
        network.network_watchers.list_all.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'networkwatcher:watchers'
        w.add_resource.assert_not_called()

    def test_flow_log_list_failure_for_one_watcher_does_not_abort_the_others(self):
        w = MagicMock()
        bad = _watcher(rid='.../networkWatchers/bad', name='bad')
        good = _watcher(rid='.../networkWatchers/good', name='good')
        good_fl = _flow_log()
        network = MagicMock()
        network.network_watchers.list_all.return_value = [bad, good]

        def _list(rg, name):
            if name == 'bad':
                raise RuntimeError('boom')
            return [good_fl]
        network.flow_logs.list.side_effect = _list
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'networkwatcher:flow_logs:bad'
        resource_types = [c.kwargs['resource_type'] for c in w.add_resource.call_args_list]
        assert resource_types == ['network_watcher', 'network_watcher', 'flow_log']

    def test_no_watchers_gathers_nothing(self):
        w = MagicMock()
        network = MagicMock()
        network.network_watchers.list_all.return_value = []
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, watcher, fl, w=None):
        w = w or MagicMock()
        network = MagicMock()
        network.network_watchers.list_all.return_value = [watcher]
        network.flow_logs.list.return_value = [fl]
        with patch('azure.mgmt.network.NetworkManagementClient', return_value=network):
            m.gather('cred', 'sub-1', w)
        return w

    def test_a_flow_logs_storage_account_gets_an_exports_to_edge(self):
        fl = _flow_log(storage_id='/subscriptions/s1/.../storageAccounts/sa1')
        w = self._gather(_watcher(), fl)
        assert {'from_type': 'flow_log', 'from_id': fl.id, 'to_type': 'storage_account',
                'to_id': '/subscriptions/s1/.../storageAccounts/sa1', 'relationship': 'exports_to'} in [c.kwargs for c in w.add_edge.call_args_list]

    def test_an_nsg_target_gets_a_monitors_edge_typed_as_nsg(self):
        fl = _flow_log(target_resource_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg1')
        w = self._gather(_watcher(), fl)
        w.add_edge.assert_called_once_with(
            from_type='flow_log', from_id=fl.id, to_type='nsg',
            to_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg1',
            relationship='monitors',
        )

    def test_a_subnet_target_gets_a_monitors_edge_typed_as_subnet_not_vnet(self):
        # Confirms the subnet pattern takes precedence over the vnet
        # pattern (both match a subnet's own longer ARM path).
        fl = _flow_log(target_resource_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet1/subnets/subnet1')
        w = self._gather(_watcher(), fl)
        w.add_edge.assert_called_once_with(
            from_type='flow_log', from_id=fl.id, to_type='subnet',
            to_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet1/subnets/subnet1',
            relationship='monitors',
        )

    def test_a_vnet_target_gets_a_monitors_edge_typed_as_virtual_network(self):
        fl = _flow_log(target_resource_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet1')
        w = self._gather(_watcher(), fl)
        w.add_edge.assert_called_once_with(
            from_type='flow_log', from_id=fl.id, to_type='virtual_network',
            to_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/virtualNetworks/vnet1',
            relationship='monitors',
        )

    def test_no_storage_id_or_target_gets_no_edges(self):
        fl = _flow_log()
        w = self._gather(_watcher(), fl)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_flow_log_gets_no_edges(self):
        fl = _flow_log(storage_id='/subscriptions/s1/.../storageAccounts/sa1',
                        target_resource_id='/subscriptions/s1/resourceGroups/rg/providers/Microsoft.Network/networkSecurityGroups/nsg1')
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(_watcher(), fl, w=w)
        w.add_edge.assert_not_called()
