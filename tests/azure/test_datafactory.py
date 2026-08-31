"""Unit tests for lensix_inventory.azure.datafactory — Data Factory instances."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.datafactory as m


def _factory(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.DataFactory/factories/f1', name='f1'):
    factory = MagicMock()
    factory.location = location
    factory.id = rid
    factory.name = name
    factory.as_dict.return_value = {'id': rid, 'name': name}
    return factory


class TestGather:
    def test_adds_one_resource_per_factory(self):
        w = MagicMock()
        factory = _factory()
        client = MagicMock()
        client.factories.list.return_value = [factory]
        with patch.object(m, 'DataFactoryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='data_factory', region='eastus', resource_id=factory.id,
            resource_name='f1', scope_id='my-rg', raw={'id': factory.id, 'name': 'f1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        factory = _factory()
        factory.as_dict.return_value = {'id': factory.id, 'name': 'f1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.factories.list.return_value = [factory]
        with patch.object(m, 'DataFactoryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        factory = _factory(location=None)
        client = MagicMock()
        client.factories.list.return_value = [factory]
        with patch.object(m, 'DataFactoryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_no_factories_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.factories.list.return_value = []
        with patch.object(m, 'DataFactoryManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
