"""Unit tests for lensix_inventory.aws.emr — EMR clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.emr as m


def _emr_client(summaries, detail_by_id=None, detail_error_ids=None,
                 secconfig_by_name=None, secconfig_error_names=None):
    import json
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Clusters': summaries}]
    detail_by_id = detail_by_id or {}
    detail_error_ids = detail_error_ids or set()
    secconfig_by_name = secconfig_by_name or {}
    secconfig_error_names = secconfig_error_names or set()

    def _describe(ClusterId):
        if ClusterId in detail_error_ids:
            raise RuntimeError('boom')
        return {'Cluster': detail_by_id[ClusterId]}
    client.describe_cluster.side_effect = _describe

    def _secconfig(Name):
        if Name in secconfig_error_names:
            raise RuntimeError('boom')
        return {'SecurityConfiguration': json.dumps(secconfig_by_name[Name])}
    client.describe_security_configuration.side_effect = _secconfig
    return client


class TestGather:
    def test_adds_one_resource_per_cluster_with_no_security_config(self):
        w = MagicMock()
        cluster = {'Id': 'j-1', 'Name': 'analytics'}
        client = _emr_client([{'Id': 'j-1'}], detail_by_id={'j-1': cluster})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once()
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'j-1'
        assert kwargs['resource_name'] == 'analytics'
        assert kwargs['raw']['_SecurityConfig'] is None

    def test_a_named_security_config_is_merged_in(self):
        w = MagicMock()
        cluster = {'Id': 'j-1', 'Name': 'analytics', 'SecurityConfiguration': 'sc-1'}
        client = _emr_client([{'Id': 'j-1'}], detail_by_id={'j-1': cluster}, secconfig_by_name={'sc-1': {'EncryptionConfiguration': {}}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_SecurityConfig'] == {'EncryptionConfiguration': {}}

    def test_a_security_config_fetch_failure_falls_back_to_none_and_is_recorded(self):
        w = MagicMock()
        cluster = {'Id': 'j-1', 'Name': 'analytics', 'SecurityConfiguration': 'sc-1'}
        client = _emr_client([{'Id': 'j-1'}], detail_by_id={'j-1': cluster}, secconfig_error_names={'sc-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        _, kwargs = w.add_resource.call_args
        assert kwargs['raw']['_SecurityConfig'] is None

    def test_a_describe_cluster_failure_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'Id': 'good', 'Name': 'good'}
        client = _emr_client([{'Id': 'bad'}, {'Id': 'good'}], detail_by_id={'good': good}, detail_error_ids={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'emr_cluster:bad'
        w.add_resource.assert_called_once()

    def test_the_original_cluster_dict_is_not_mutated(self):
        w = MagicMock()
        cluster = {'Id': 'j-1', 'Name': 'analytics'}
        client = _emr_client([{'Id': 'j-1'}], detail_by_id={'j-1': cluster})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_SecurityConfig' not in cluster
