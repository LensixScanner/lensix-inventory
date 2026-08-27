"""Unit tests for lensix_inventory.azure.servicebus — Service Bus namespaces."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.servicebus as m


def _namespace(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.ServiceBus/namespaces/ns1', name='ns1'):
    ns = MagicMock()
    ns.location = location
    ns.id = rid
    ns.name = name
    ns.as_dict.return_value = {'id': rid, 'name': name}
    return ns


class TestGather:
    def test_adds_one_resource_per_namespace(self):
        w = MagicMock()
        ns = _namespace()
        client = MagicMock()
        client.namespaces.list.return_value = [ns]
        with patch('azure.mgmt.servicebus.ServiceBusManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='servicebus_namespace', region='eastus', resource_id=ns.id,
            resource_name='ns1', scope_id='my-rg', raw={'id': ns.id, 'name': 'ns1'},
        )

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        ns = _namespace(location=None)
        client = MagicMock()
        client.namespaces.list.return_value = [ns]
        with patch('azure.mgmt.servicebus.ServiceBusManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        client = MagicMock()
        client.namespaces.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.servicebus.ServiceBusManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'servicebus:namespaces'
        w.add_resource.assert_not_called()

    def test_no_namespaces_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.namespaces.list.return_value = []
        with patch('azure.mgmt.servicebus.ServiceBusManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
