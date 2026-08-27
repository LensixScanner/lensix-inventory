"""Unit tests for lensix_inventory.aws.elasticache — replication groups and cache clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.elasticache as m


def _ec_client(rg_pages=None, cluster_pages=None, rg_raises=False, cluster_raises=False):
    client = MagicMock()
    if rg_raises:
        client.describe_replication_groups.side_effect = RuntimeError('boom')
    else:
        client.describe_replication_groups.side_effect = rg_pages or [{'ReplicationGroups': []}]
    if cluster_raises:
        client.describe_cache_clusters.side_effect = RuntimeError('boom')
    else:
        client.describe_cache_clusters.side_effect = cluster_pages or [{'CacheClusters': []}]
    return client


class TestGetReplicationGroups:
    def test_paginates_via_marker(self):
        client = _ec_client(rg_pages=[
            {'ReplicationGroups': [{'ReplicationGroupId': 'rg1'}], 'Marker': 'tok'},
            {'ReplicationGroups': [{'ReplicationGroupId': 'rg2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            groups = m.get_replication_groups('us-east-1')
        assert [g['ReplicationGroupId'] for g in groups] == ['rg1', 'rg2']


class TestGetCacheClusters:
    def test_paginates_via_marker(self):
        client = _ec_client(cluster_pages=[
            {'CacheClusters': [{'CacheClusterId': 'c1'}], 'Marker': 'tok'},
            {'CacheClusters': [{'CacheClusterId': 'c2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            clusters = m.get_cache_clusters('us-east-1')
        assert [c['CacheClusterId'] for c in clusters] == ['c1', 'c2']


class TestGather:
    def test_adds_one_resource_per_replication_group(self):
        w = MagicMock()
        rg = {'ReplicationGroupId': 'rg1', 'ARN': 'arn:aws:elasticache:us-east-1:1:replicationgroup:rg1'}
        client = _ec_client(rg_pages=[{'ReplicationGroups': [rg]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['elasticache_replication_group'].kwargs['resource_id'] == 'arn:aws:elasticache:us-east-1:1:replicationgroup:rg1'

    def test_adds_one_resource_per_cache_cluster(self):
        w = MagicMock()
        cluster = {'CacheClusterId': 'c1', 'ARN': 'arn:aws:elasticache:us-east-1:1:cluster:c1'}
        client = _ec_client(cluster_pages=[{'CacheClusters': [cluster]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['elasticache_cluster'].kwargs['resource_id'] == 'arn:aws:elasticache:us-east-1:1:cluster:c1'

    def test_falls_back_to_the_bare_id_when_arn_missing(self):
        w = MagicMock()
        client = _ec_client(rg_pages=[{'ReplicationGroups': [{'ReplicationGroupId': 'rg1'}]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'rg1'

    def test_a_replication_groups_failure_does_not_prevent_cache_clusters_from_being_gathered(self):
        w = MagicMock()
        cluster = {'CacheClusterId': 'c1'}
        client = _ec_client(rg_raises=True, cluster_pages=[{'CacheClusters': [cluster]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'elasticache (replication groups)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'elasticache_cluster' in calls

    def test_a_cache_clusters_failure_does_not_prevent_replication_groups_from_being_gathered(self):
        w = MagicMock()
        rg = {'ReplicationGroupId': 'rg1'}
        client = _ec_client(rg_pages=[{'ReplicationGroups': [rg]}], cluster_raises=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'elasticache (cache clusters)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'elasticache_replication_group' in calls
