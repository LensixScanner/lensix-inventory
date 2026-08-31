"""Unit tests for lensix_inventory.azure.appgateway — Application Gateways."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.appgateway as m


def _gateway(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Network/applicationGateways/gw1', name='gw1'):
    gw = MagicMock()
    gw.location = location
    gw.id = rid
    gw.name = name
    gw.as_dict.return_value = {'id': rid, 'name': name}
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
