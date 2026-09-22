"""Unit tests for lensix_inventory.azure.apimgmt — API Management services."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.apimgmt as m


def _service(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.ApiManagement/service/svc1', name='svc1',
             subnet_id=None):
    svc = MagicMock()
    svc.location = location
    svc.id = rid
    svc.name = name
    raw = {'id': rid, 'name': name}
    if subnet_id is not None:
        raw['virtual_network_configuration'] = {'subnet_resource_id': subnet_id}
    svc.as_dict.return_value = raw
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
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        service = _service()
        service.as_dict.return_value = {'id': service.id, 'name': 'svc1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.api_management_service.list.return_value = [service]
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_no_services_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.api_management_service.list.return_value = []
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()


class TestGatherEdges:
    def test_a_service_with_vnet_integration_gets_an_in_subnet_edge(self):
        w = MagicMock()
        service = _service(subnet_id='/subscriptions/s1/.../subnets/apim-subnet')
        client = MagicMock()
        client.api_management_service.list.return_value = [service]
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_called_once_with(
            from_type='apimgmt_service', from_id=service.id, to_type='subnet',
            to_id='/subscriptions/s1/.../subnets/apim-subnet', relationship='in_subnet',
        )

    def test_a_service_with_no_vnet_integration_gets_no_edge(self):
        w = MagicMock()
        service = _service()
        client = MagicMock()
        client.api_management_service.list.return_value = [service]
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_service_gets_no_edge(self):
        w = MagicMock()
        w.add_resource.return_value = False
        service = _service(subnet_id='/subscriptions/s1/.../subnets/apim-subnet')
        client = MagicMock()
        client.api_management_service.list.return_value = [service]
        with patch.object(m, 'ApiManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_edge.assert_not_called()
