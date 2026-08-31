"""Unit tests for lensix_inventory.azure.acr — Container Registries."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.acr as m


def _registry(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.ContainerRegistry/registries/r1', name='r1'):
    reg = MagicMock()
    reg.location = location
    reg.id = rid
    reg.name = name
    reg.as_dict.return_value = {'id': rid, 'name': name}
    return reg


class TestGather:
    def test_adds_one_resource_per_registry(self):
        w = MagicMock()
        registry = _registry()
        client = MagicMock()
        client.registries.list.return_value = [registry]
        with patch.object(m, 'ContainerRegistryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='container_registry', region='eastus', resource_id=registry.id,
            resource_name='r1', scope_id='my-rg', raw={'id': registry.id, 'name': 'r1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        registry = _registry()
        registry.as_dict.return_value = {'id': registry.id, 'name': 'r1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.registries.list.return_value = [registry]
        with patch.object(m, 'ContainerRegistryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        registry = _registry(location=None)
        client = MagicMock()
        client.registries.list.return_value = [registry]
        with patch.object(m, 'ContainerRegistryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_no_registries_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.registries.list.return_value = []
        with patch.object(m, 'ContainerRegistryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
