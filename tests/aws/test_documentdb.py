"""Unit tests for lensix_inventory.aws.documentdb — DocumentDB clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.documentdb as m


def _rds(clusters):
    rds = MagicMock()
    rds.get_paginator.return_value.paginate.return_value = [{'DBClusters': clusters}]
    return rds


class TestGetDocdbClusters:
    def test_filters_to_docdb_engine_only(self):
        rds = _rds([{'Engine': 'docdb', 'DBClusterIdentifier': 'c1'}, {'Engine': 'aurora-mysql', 'DBClusterIdentifier': 'c2'}])
        with patch.object(m.boto3, 'client', return_value=rds):
            clusters = m.get_docdb_clusters('us-east-1')
        assert [c['DBClusterIdentifier'] for c in clusters] == ['c1']


class TestGather:
    def test_adds_one_resource_per_docdb_cluster(self):
        w = MagicMock()
        cluster = {'Engine': 'docdb', 'DBClusterArn': 'arn:aws:rds:us-east-1:1:cluster:c1', 'DBClusterIdentifier': 'c1'}
        rds = _rds([cluster])
        with patch.object(m.boto3, 'client', return_value=rds):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='docdb_cluster', region='us-east-1',
            resource_id='arn:aws:rds:us-east-1:1:cluster:c1', resource_name='c1', raw=cluster, tags=None,
        )

    def test_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = {'Engine': 'docdb', 'DBClusterArn': 'arn:1', 'DBClusterIdentifier': 'c1',
                   'TagList': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        rds = _rds([cluster])
        with patch.object(m.boto3, 'client', return_value=rds):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == cluster['TagList']

    def test_no_docdb_clusters_gathers_nothing(self):
        w = MagicMock()
        rds = _rds([{'Engine': 'aurora-mysql', 'DBClusterIdentifier': 'c2'}])
        with patch.object(m.boto3, 'client', return_value=rds):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
