"""Unit tests for lensix_inventory.azure.bastion — Bastion hosts."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.bastion as m


def _host(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/bastionHosts/b1', name='b1'):
    host = MagicMock()
    host.location = location
    host.id = rid
    host.name = name
    host.as_dict.return_value = {'id': rid, 'name': name}
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
