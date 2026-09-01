"""Unit tests for lensix_inventory.aws.redshift — Redshift clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.redshift as m


def _redshift_client(clusters, logging_by_id=None, logging_raises_ids=None,
                      params_by_group=None, params_raises_groups=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Clusters': clusters}]

    logging_by_id = logging_by_id or {}
    logging_raises_ids = logging_raises_ids or set()

    def _logging(ClusterIdentifier):
        if ClusterIdentifier in logging_raises_ids:
            raise RuntimeError('boom')
        return logging_by_id.get(ClusterIdentifier, {'LoggingEnabled': False})
    client.describe_logging_status.side_effect = _logging

    params_by_group = params_by_group or {}
    params_raises_groups = params_raises_groups or set()

    def _param_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_cluster_parameters':
            def _paginate(ParameterGroupName):
                if ParameterGroupName in params_raises_groups:
                    raise RuntimeError('boom')
                return [{'Parameters': params_by_group.get(ParameterGroupName, [])}]
            p.paginate.side_effect = _paginate
        return p
    client.get_paginator.side_effect = lambda op: (
        MagicMock(paginate=MagicMock(return_value=[{'Clusters': clusters}])) if op == 'describe_clusters'
        else _param_paginator(op)
    )
    return client


class TestGetSslParameters:
    def test_returns_the_require_ssl_parameter(self):
        client = _redshift_client([], params_by_group={'pg-1': [{'ParameterName': 'require_ssl', 'ParameterValue': 'true'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            param = m.get_ssl_parameters('us-east-1', 'pg-1')
        assert param == {'ParameterName': 'require_ssl', 'ParameterValue': 'true'}

    def test_returns_none_when_not_found(self):
        client = _redshift_client([], params_by_group={'pg-1': [{'ParameterName': 'other'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_ssl_parameters('us-east-1', 'pg-1') is None

    def test_returns_none_on_failure(self):
        client = _redshift_client([], params_raises_groups={'pg-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_ssl_parameters('us-east-1', 'pg-1') is None


class TestGetLoggingStatus:
    def test_returns_none_on_failure_rather_than_raising(self):
        client = _redshift_client([], logging_raises_ids={'c1'})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_logging_status('us-east-1', 'c1') is None


class TestGather:
    def test_adds_one_resource_with_logging_and_ssl_status_merged_in(self):
        w = MagicMock()
        cluster = {
            'ClusterIdentifier': 'c1', 'ClusterNamespaceArn': 'arn:aws:redshift:us-east-1:1:namespace:c1',
            'VpcId': 'vpc-1', 'ClusterParameterGroups': [{'ParameterGroupName': 'pg-1'}],
        }
        client = _redshift_client(
            [cluster],
            logging_by_id={'c1': {'LoggingEnabled': True}},
            params_by_group={'pg-1': [{'ParameterName': 'require_ssl', 'ParameterValue': 'true'}]},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'arn:aws:redshift:us-east-1:1:namespace:c1'
        assert kwargs['resource_name'] == 'c1'
        assert kwargs['scope_id'] == 'vpc-1'
        assert kwargs['raw']['_LoggingStatus'] == {'LoggingEnabled': True}
        assert kwargs['raw']['_SSLParameters']['pg-1']['ParameterName'] == 'require_ssl'

    def test_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = {'ClusterIdentifier': 'c1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        client = _redshift_client([cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == cluster['Tags']

    def test_falls_back_to_cluster_identifier_when_namespace_arn_missing(self):
        w = MagicMock()
        cluster = {'ClusterIdentifier': 'c1'}
        client = _redshift_client([cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'c1'

    def test_a_logging_status_failure_does_not_prevent_the_resource_from_being_recorded(self):
        w = MagicMock()
        cluster = {'ClusterIdentifier': 'c1'}
        client = _redshift_client([cluster], logging_raises_ids={'c1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_LoggingStatus'] is None

    def test_the_original_cluster_dict_is_not_mutated(self):
        w = MagicMock()
        cluster = {'ClusterIdentifier': 'c1'}
        client = _redshift_client([cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_LoggingStatus' not in cluster

    def test_a_cluster_whose_namespace_arn_is_protected_is_stamped_true(self):
        w = MagicMock()
        arn = 'arn:aws:redshift:us-east-1:1:namespace:c1'
        cluster = {'ClusterIdentifier': 'c1', 'ClusterNamespaceArn': arn}
        client = _redshift_client([cluster])
        with patch.object(m, 'get_protected_resource_arns', return_value={arn}):
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['raw']['_ProtectedByAwsBackup'] is True

    def test_a_cluster_whose_namespace_arn_is_not_protected_is_stamped_false(self):
        w = MagicMock()
        arn = 'arn:aws:redshift:us-east-1:1:namespace:c1'
        cluster = {'ClusterIdentifier': 'c1', 'ClusterNamespaceArn': arn}
        client = _redshift_client([cluster])
        with patch.object(m, 'get_protected_resource_arns', return_value=set()):
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['raw']['_ProtectedByAwsBackup'] is False

    def test_a_backup_lookup_failure_stamps_false_and_records_an_error_but_does_not_abort_gather(self):
        w = MagicMock()
        cluster = {'ClusterIdentifier': 'c1', 'ClusterNamespaceArn': 'arn:aws:redshift:us-east-1:1:namespace:c1'}
        client = _redshift_client([cluster])
        with patch.object(m, 'get_protected_resource_arns', side_effect=RuntimeError('AccessDenied')):
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'redshift (aws backup protected resources)' for c in w.add_error.call_args_list)
        assert w.add_resource.call_args.kwargs['raw']['_ProtectedByAwsBackup'] is False
