"""Unit tests for lensix_inventory.aws.eks — EKS clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.eks as m


def _eks_client(names, detail_by_name=None, detail_error_names=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'clusters': names}]
    detail_by_name = detail_by_name or {}
    detail_error_names = detail_error_names or set()

    def _describe(name):
        if name in detail_error_names:
            raise RuntimeError('boom')
        return {'cluster': detail_by_name[name]}
    client.describe_cluster.side_effect = lambda name: _describe(name)
    return client


class TestGather:
    def test_adds_one_resource_per_cluster(self):
        w = MagicMock()
        cluster = {'arn': 'arn:aws:eks:us-east-1:1:cluster/c1', 'resourcesVpcConfig': {'vpcId': 'vpc-1'}}
        client = _eks_client(['c1'], detail_by_name={'c1': cluster})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='eks_cluster', region='us-east-1',
            resource_id='arn:aws:eks:us-east-1:1:cluster/c1', resource_name='c1',
            scope_id='vpc-1', raw=cluster, tags=None,
        )

    def test_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = {'arn': 'arn:1', 'resourcesVpcConfig': {}, 'tags': {'lensix-suppress': 'true'}}
        client = _eks_client(['c1'], detail_by_name={'c1': cluster})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_the_cluster_name_when_arn_missing(self):
        w = MagicMock()
        cluster = {'resourcesVpcConfig': {}}
        client = _eks_client(['c1'], detail_by_name={'c1': cluster})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'c1'

    def test_a_describe_failure_for_one_cluster_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'arn': 'arn:c2', 'resourcesVpcConfig': {}}
        client = _eks_client(['bad', 'good'], detail_by_name={'good': good}, detail_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'eks_cluster:bad'
        w.add_resource.assert_called_once()

    def test_no_clusters_gathers_nothing(self):
        w = MagicMock()
        client = _eks_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
