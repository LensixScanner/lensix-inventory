"""Unit tests for lensix_inventory.azure.eventhub — Event Hub namespaces."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.eventhub as m


def _namespace(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.EventHub/namespaces/ns1', name='ns1'):
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
        with patch.object(m, 'EventHubManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='eventhub_namespace', region='eastus', resource_id=ns.id,
            resource_name='ns1', scope_id='my-rg', raw={'id': ns.id, 'name': 'ns1'},
        )

    def test_no_namespaces_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.namespaces.list.return_value = []
        with patch.object(m, 'EventHubManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
