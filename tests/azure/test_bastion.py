"""Unit tests for lensix_inventory.azure.bastion — Bastion hosts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.bastion as m


def _host(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/bastionHosts/b1', name='b1',
          subnet_ids=None, vnet_id=None):
    host = MagicMock()
    host.location = location
    host.id = rid
    host.name = name
    raw = {'id': rid, 'name': name}
    if subnet_ids is not None:
        raw['ip_configurations'] = [{'subnet': {'id': sid}} for sid in subnet_ids]
    if vnet_id is not None:
        raw['virtual_network'] = {'id': vnet_id}
    host.as_dict.return_value = raw
    return host


class TestGather:
    def test_adds_one_resource_per_host(self):
        w = MagicMock()
        host = _host()
        client = MagicMock()
        client.bastion_hosts.list_all.return_value = [host]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='bastion_host', region='eastus', resource_id=host.id,
            resource_name='b1', scope_id='my-rg', raw={'id': host.id, 'name': 'b1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        host = _host()
        host.as_dict.return_value = {'id': host.id, 'name': 'b1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.bastion_hosts.list_all.return_value = [host]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_bastion_hosts_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.bastion_hosts.list_all.return_value = []
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def _gather(self, host, w=None):
        w = w or MagicMock()
        client = MagicMock()
        client.bastion_hosts.list_all.return_value = [host]
        with patch.object(m, 'NetworkManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        return w

    def test_an_ip_config_subnet_gets_an_in_subnet_edge(self):
        host = _host(subnet_ids=['subnet-1'])
        w = self._gather(host)
        w.add_edge.assert_called_once_with(
            from_type='bastion_host', from_id=host.id, to_type='subnet', to_id='subnet-1', relationship='in_subnet',
        )

    def test_the_hosts_own_vnet_gets_an_in_vnet_edge(self):
        host = _host(vnet_id='vnet-1')
        w = self._gather(host)
        w.add_edge.assert_called_once_with(
            from_type='bastion_host', from_id=host.id, to_type='virtual_network', to_id='vnet-1', relationship='in_vnet',
        )

    def test_no_ip_configs_or_vnet_gets_no_edges(self):
        host = _host()
        w = self._gather(host)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_host_gets_no_edges(self):
        host = _host(subnet_ids=['subnet-1'], vnet_id='vnet-1')
        w = MagicMock()
        w.add_resource.return_value = False
        self._gather(host, w=w)
        w.add_edge.assert_not_called()
