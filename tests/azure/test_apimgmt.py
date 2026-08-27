"""Unit tests for lensix_inventory.azure.apimgmt — API Management services."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.apimgmt as m


def _service(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.ApiManagement/service/svc1', name='svc1'):
    svc = MagicMock()
    svc.location = location
    svc.id = rid
    svc.name = name
    svc.as_dict.return_value = {'id': rid, 'name': name}
    return svc


class TestGather:
    def test_adds_one_resource_per_service(self):
        w = MagicMock()
        service = _service()
        client = MagicMock()
        client.api_management_service.list.return_value = [service]
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='apimgmt_service', region='eastus', resource_id=service.id,
            resource_name='svc1', scope_id='my-rg', raw={'id': service.id, 'name': 'svc1'},
        )

    def test_no_services_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.api_management_service.list.return_value = []
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
