"""Unit tests for lensix_inventory.azure.redis — Azure Cache for Redis."""

from unittest.mock import MagicMock, patch

import lensix_inventory.azure.redis as m


def _cache(location='eastus', rid='/subscriptions/s1/resourceGroups/my-rg/providers/Microsoft.Cache/Redis/c1', name='c1'):
    cache = MagicMock()
    cache.location = location
    cache.id = rid
    cache.name = name
    cache.as_dict.return_value = {'id': rid, 'name': name}
    return cache


class TestGather:
    def test_adds_one_resource_per_cache(self):
        w = MagicMock()
        cache = _cache()
        client = MagicMock()
        client.redis.list.return_value = [cache]
        with patch('azure.mgmt.redis.RedisManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='redis_cache', region='eastus', resource_id=cache.id,
            resource_name='c1', scope_id='my-rg', raw={'id': cache.id, 'name': 'c1'},
            tags=None,
        )

    def test_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cache = _cache()
        cache.as_dict.return_value = {'id': cache.id, 'name': 'c1', 'tags': {'lensix-suppress': 'true'}}
        client = MagicMock()
        client.redis.list.return_value = [cache]
        with patch('azure.mgmt.redis.RedisManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_global_region_without_a_location(self):
        w = MagicMock()
        cache = _cache(location=None)
        client = MagicMock()
        client.redis.list.return_value = [cache]
        with patch('azure.mgmt.redis.RedisManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['region'] == 'global'

    def test_a_fetch_failure_is_recorded_and_gather_returns_without_raising(self):
        w = MagicMock()
        client = MagicMock()
        client.redis.list.side_effect = RuntimeError('boom')
        with patch('azure.mgmt.redis.RedisManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'redis:caches'
        w.add_resource.assert_not_called()

    def test_no_caches_gathers_nothing(self):
        w = MagicMock()
        client = MagicMock()
        client.redis.list.return_value = []
        with patch('azure.mgmt.redis.RedisManagementClient', return_value=client):
            m.gather('cred', 'sub-1', w)
        w.add_resource.assert_not_called()
