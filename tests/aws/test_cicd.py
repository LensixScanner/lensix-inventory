"""Unit tests for lensix_inventory.aws.cicd — CodeCommit repos and CodeBuild projects."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.cicd as m


def _client(repos=None, cb_names=None, cb_projects=None, repos_raise=False, cb_raise=False):
    client = MagicMock()
    if repos_raise:
        client.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
    else:
        client.get_paginator.return_value.paginate.return_value = [{'repositories': repos or []}]

    if cb_raise:
        client.list_projects.side_effect = RuntimeError('boom')
    else:
        client.list_projects.side_effect = [{'projects': cb_names or []}]
    client.batch_get_projects.return_value = {'projects': cb_projects or []}
    return client


class TestRedactProject:
    def test_keeps_only_name_and_type_for_environment_variables(self):
        project = {'name': 'p1', 'environment': {'environmentVariables': [
            {'name': 'DB_PASS', 'type': 'PLAINTEXT', 'value': 'hunter2'},
        ]}}
        raw, hits = m._redact_project(project)
        assert raw['environment']['environmentVariables'] == [{'name': 'DB_PASS', 'type': 'PLAINTEXT'}]

    def test_detects_a_secret_looking_value(self):
        project = {'name': 'p1', 'environment': {'environmentVariables': [
            {'name': 'KEY', 'type': 'PLAINTEXT', 'value': 'sk_live_' + 'a' * 24},
        ]}}
        raw, hits = m._redact_project(project)
        assert hits == ['Stripe Live API Key']

    def test_no_environment_block_at_all(self):
        project = {'name': 'p1'}
        raw, hits = m._redact_project(project)
        assert raw == project
        assert hits == []

    def test_the_original_project_dict_is_not_mutated(self):
        project = {'name': 'p1', 'environment': {'environmentVariables': [{'name': 'X', 'value': 'y', 'type': 'PLAINTEXT'}]}}
        m._redact_project(project)
        assert project['environment']['environmentVariables'][0]['value'] == 'y'


class TestGetCodebuildProjects:
    def test_batches_batch_get_projects_calls_in_groups_of_100(self):
        names = [f'p{i}' for i in range(150)]
        client = _client(cb_names=names, cb_projects=[{'name': 'x'}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.get_codebuild_projects('us-east-1')
        assert client.batch_get_projects.call_count == 2


class TestGather:
    def test_adds_one_resource_per_codecommit_repo(self):
        w = MagicMock()
        repo = {'repositoryId': 'r1', 'repositoryName': 'my-repo'}
        client = _client(repos=[repo])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['codecommit_repo'].kwargs['resource_id'] == 'r1'
        assert calls['codecommit_repo'].kwargs['resource_name'] == 'my-repo'

    def test_adds_one_resource_per_codebuild_project_with_redacted_env(self):
        w = MagicMock()
        project = {'name': 'p1', 'arn': 'arn:aws:codebuild:us-east-1:1:project/p1',
                   'environment': {'environmentVariables': [{'name': 'X', 'type': 'PLAINTEXT', 'value': 'y'}]}}
        client = _client(cb_names=['p1'], cb_projects=[project])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        proj_call = calls['codebuild_project']
        assert proj_call.kwargs['resource_id'] == 'arn:aws:codebuild:us-east-1:1:project/p1'
        assert proj_call.kwargs['raw']['environment']['environmentVariables'] == [{'name': 'X', 'type': 'PLAINTEXT'}]

    def test_a_codecommit_failure_does_not_prevent_codebuild_from_being_gathered(self):
        w = MagicMock()
        project = {'name': 'p1'}
        client = _client(repos_raise=True, cb_names=['p1'], cb_projects=[project])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'cicd (codecommit repos)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'codebuild_project' in calls

    def test_a_codebuild_failure_does_not_prevent_codecommit_from_being_gathered(self):
        w = MagicMock()
        repo = {'repositoryId': 'r1', 'repositoryName': 'my-repo'}
        client = _client(repos=[repo], cb_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'cicd (codebuild projects)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'codecommit_repo' in calls
