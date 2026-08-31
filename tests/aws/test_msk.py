"""Unit tests for lensix_inventory.aws.msk — MSK (Kafka) clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.msk as m


def _msk_client(v2_pages, detail_by_arn=None, detail_error_arns=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = v2_pages
    detail_by_arn = detail_by_arn or {}
    detail_error_arns = detail_error_arns or set()

    def _describe(ClusterArn):
        if ClusterArn in detail_error_arns:
            raise RuntimeError('boom')
        return {'ClusterInfo': detail_by_arn[ClusterArn]}
    client.describe_cluster_v2.side_effect = _describe
    return client


class TestGetClusters:
    def test_uses_the_v2_paginator_when_available(self):
        client = _msk_client([{'ClusterInfoList': [{'ClusterArn': 'arn:c1'}]}])
        with patch.object(m.boto3, 'client', return_value=client):
            clusters = m.get_clusters('us-east-1')
        assert [c['ClusterArn'] for c in clusters] == ['arn:c1']

    def test_falls_back_to_list_clusters_when_v2_paginator_is_unavailable(self):
        client = MagicMock()
        client.get_paginator.side_effect = Exception('v2 not supported')
        client.list_clusters.return_value = {'ClusterInfoList': [{'ClusterArn': 'arn:c1'}]}
        with patch.object(m.boto3, 'client', return_value=client):
            clusters = m.get_clusters('us-east-1')
        assert [c['ClusterArn'] for c in clusters] == ['arn:c1']


class TestGather:
    def test_adds_one_resource_per_cluster_using_the_describe_result(self):
        w = MagicMock()
        summary = {'ClusterArn': 'arn:c1', 'ClusterName': 'my-cluster'}
        detail = {'ClusterArn': 'arn:c1', 'ClusterName': 'my-cluster', 'State': 'ACTIVE'}
        client = _msk_client([{'ClusterInfoList': [summary]}], detail_by_arn={'arn:c1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='msk_cluster', region='us-east-1',
            resource_id='arn:c1', resource_name='my-cluster', raw=detail, tags=None,
        )

    def test_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        summary = {'ClusterArn': 'arn:c1', 'ClusterName': 'my-cluster'}
        detail = {'ClusterArn': 'arn:c1', 'ClusterName': 'my-cluster', 'Tags': {'lensix-suppress': 'true'}}
        client = _msk_client([{'ClusterInfoList': [summary]}], detail_by_arn={'arn:c1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_a_describe_failure_falls_back_to_the_summary_as_raw_rather_than_skipping(self):
        w = MagicMock()
        summary = {'ClusterArn': 'arn:c1', 'ClusterName': 'my-cluster'}
        client = _msk_client([{'ClusterInfoList': [summary]}], detail_error_arns={'arn:c1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_error.assert_called_once()
        assert w.add_error.call_args.kwargs['source'] == 'msk_cluster:arn:c1'
        w.add_resource.assert_called_once_with(
            resource_type='msk_cluster', region='us-east-1',
            resource_id='arn:c1', resource_name='my-cluster', raw=summary, tags=None,
        )

    def test_no_clusters_gathers_nothing(self):
        w = MagicMock()
        client = _msk_client([{'ClusterInfoList': []}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
