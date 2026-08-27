"""Unit tests for lensix_inventory.aws.neptune — Neptune DB clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.neptune as m


def _neptune(clusters):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'DBClusters': clusters}]
    return client


class TestGetClusters:
    def test_filters_to_neptune_engine_only(self):
        client = _neptune([{'Engine': 'neptune', 'DBClusterIdentifier': 'c1'}, {'Engine': 'docdb', 'DBClusterIdentifier': 'c2'}])
        with patch.object(m.boto3, 'client', return_value=client):
            clusters = m.get_clusters('us-east-1')
        assert [c['DBClusterIdentifier'] for c in clusters] == ['c1']


class TestGather:
    def test_adds_one_resource_per_neptune_cluster(self):
        w = MagicMock()
        cluster = {'Engine': 'neptune', 'DBClusterArn': 'arn:aws:rds:us-east-1:1:cluster:c1', 'DBClusterIdentifier': 'c1'}
        with patch.object(m.boto3, 'client', return_value=_neptune([cluster])):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='neptune_cluster', region='us-east-1',
            resource_id='arn:aws:rds:us-east-1:1:cluster:c1', resource_name='c1', raw=cluster,
        )

    def test_falls_back_to_arn_as_name_when_identifier_missing(self):
        w = MagicMock()
        cluster = {'Engine': 'neptune', 'DBClusterArn': 'arn:aws:rds:us-east-1:1:cluster:c1'}
        with patch.object(m.boto3, 'client', return_value=_neptune([cluster])):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_name'] == 'arn:aws:rds:us-east-1:1:cluster:c1'

    def test_no_neptune_clusters_gathers_nothing(self):
        w = MagicMock()
        with patch.object(m.boto3, 'client', return_value=_neptune([])):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
