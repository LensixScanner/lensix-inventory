"""Unit tests for lensix_inventory.aws.ecr — ECR repositories and registry scan config."""

import json
from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ecr as m


def _ecr_client(repos, scan_rules=None, scan_config_raises=False,
                 policy_by_name=None, policy_raises_names=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'repositories': repos}]

    if scan_config_raises:
        client.get_registry_scanning_configuration.side_effect = RuntimeError('boom')
    else:
        client.get_registry_scanning_configuration.return_value = {
            'scanningConfiguration': {'rules': scan_rules or []}
        }

    policy_by_name = policy_by_name or {}
    policy_raises_names = policy_raises_names or set()

    def _get_policy(repositoryName):
        if repositoryName in policy_raises_names:
            raise RuntimeError('boom')
        return {'policyText': json.dumps(policy_by_name[repositoryName])}
    client.get_repository_policy.side_effect = _get_policy
    return client


class TestGetRegistryScanRules:
    def test_returns_the_configured_rules(self):
        client = _ecr_client([], scan_rules=[{'scanFrequency': 'CONTINUOUS_SCAN'}])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_registry_scan_rules('us-east-1') == [{'scanFrequency': 'CONTINUOUS_SCAN'}]

    def test_returns_empty_list_on_failure_rather_than_raising(self):
        client = _ecr_client([], scan_config_raises=True)
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_registry_scan_rules('us-east-1') == []


class TestGetRepositoryPolicy:
    def test_returns_the_parsed_policy(self):
        client = _ecr_client([], policy_by_name={'my-repo': {'Statement': []}})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_repository_policy('us-east-1', 'my-repo') == {'Statement': []}

    def test_returns_none_when_no_policy_is_attached(self):
        client = _ecr_client([], policy_raises_names={'my-repo'})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_repository_policy('us-east-1', 'my-repo') is None


class TestGather:
    def test_always_adds_a_registry_scan_config_resource(self):
        w = MagicMock()
        client = _ecr_client([], scan_rules=[{'scanFrequency': 'MANUAL'}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecr_registry_scan_config'].kwargs['resource_id'] == 'ecr-scanconfig-us-east-1'
        assert calls['ecr_registry_scan_config'].kwargs['raw'] == {'rules': [{'scanFrequency': 'MANUAL'}]}

    def test_adds_one_resource_per_repository_with_its_policy_merged_in(self):
        w = MagicMock()
        repo = {'repositoryName': 'my-repo', 'repositoryArn': 'arn:aws:ecr:us-east-1:1:repository/my-repo'}
        client = _ecr_client([repo], policy_by_name={'my-repo': {'Statement': []}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        repo_call = calls['ecr_repository']
        assert repo_call.kwargs['resource_id'] == 'arn:aws:ecr:us-east-1:1:repository/my-repo'
        assert repo_call.kwargs['resource_name'] == 'my-repo'
        assert repo_call.kwargs['raw']['_RepositoryPolicy'] == {'Statement': []}

    def test_a_repository_without_a_policy_gets_none(self):
        w = MagicMock()
        repo = {'repositoryName': 'my-repo', 'repositoryArn': 'arn:1'}
        client = _ecr_client([repo], policy_raises_names={'my-repo'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecr_repository'].kwargs['raw']['_RepositoryPolicy'] is None

    def test_the_original_repository_dict_is_not_mutated(self):
        w = MagicMock()
        repo = {'repositoryName': 'my-repo', 'repositoryArn': 'arn:1'}
        client = _ecr_client([repo], policy_by_name={'my-repo': {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_RepositoryPolicy' not in repo
