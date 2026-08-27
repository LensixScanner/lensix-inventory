"""Unit tests for lensix_inventory.aws.ecs — clusters and task definitions."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ecs as m


def _client(cluster_arns=None, clusters=None, families=None, task_def_by_family=None,
            task_def_error_families=None, list_clusters_raise=False, describe_clusters_raise=False,
            list_families_raise=False):
    client = MagicMock()

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'list_clusters':
            if list_clusters_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'clusterArns': cluster_arns or []}]
        elif op_name == 'list_task_definition_families':
            if list_families_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'families': families or []}]
        return p
    client.get_paginator.side_effect = _get_paginator

    if describe_clusters_raise:
        client.describe_clusters.side_effect = RuntimeError('boom')
    else:
        client.describe_clusters.return_value = {'clusters': clusters or []}

    task_def_by_family = task_def_by_family or {}
    task_def_error_families = task_def_error_families or set()

    def _describe_task_def(taskDefinition):
        if taskDefinition in task_def_error_families:
            raise RuntimeError('boom')
        return {'taskDefinition': task_def_by_family[taskDefinition]}
    client.describe_task_definition.side_effect = _describe_task_def
    return client


class TestRedactTaskDef:
    def test_replaces_environment_with_names_only(self):
        task_def = {'containerDefinitions': [{'environment': [{'name': 'DB_PASS', 'value': 'hunter2'}]}]}
        raw, hits = m._redact_task_def(task_def)
        assert raw['containerDefinitions'][0]['environment'] == ['DB_PASS']

    def test_detects_a_secret_looking_value(self):
        task_def = {'containerDefinitions': [{'environment': [{'name': 'KEY', 'value': 'sk_live_' + 'a' * 24}]}]}
        raw, hits = m._redact_task_def(task_def)
        assert hits == ['Stripe Live API Key']

    def test_a_container_without_environment_is_left_alone(self):
        task_def = {'containerDefinitions': [{'name': 'sidecar'}]}
        raw, hits = m._redact_task_def(task_def)
        assert raw['containerDefinitions'] == [{'name': 'sidecar'}]

    def test_secrets_references_are_left_intact(self):
        task_def = {'containerDefinitions': [{'secrets': [{'name': 'DB_PASS', 'valueFrom': 'arn:aws:ssm:...'}]}]}
        raw, hits = m._redact_task_def(task_def)
        assert raw['containerDefinitions'][0]['secrets'] == [{'name': 'DB_PASS', 'valueFrom': 'arn:aws:ssm:...'}]

    def test_the_original_task_def_dict_is_not_mutated(self):
        task_def = {'containerDefinitions': [{'environment': [{'name': 'X', 'value': 'y'}]}]}
        m._redact_task_def(task_def)
        assert task_def['containerDefinitions'][0]['environment'][0]['value'] == 'y'


class TestGather:
    def test_adds_one_resource_per_cluster(self):
        w = MagicMock()
        cluster = {'clusterArn': 'arn:aws:ecs:us-east-1:1:cluster/my-cluster'}
        client = _client(cluster_arns=['arn:aws:ecs:us-east-1:1:cluster/my-cluster'], clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        cluster_call = calls['ecs_cluster']
        assert cluster_call.kwargs['resource_id'] == 'arn:aws:ecs:us-east-1:1:cluster/my-cluster'
        assert cluster_call.kwargs['resource_name'] == 'my-cluster'

    def test_no_cluster_arns_skips_the_describe_call_entirely(self):
        w = MagicMock()
        client = _client(cluster_arns=[])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        client.describe_clusters.assert_not_called()

    def test_a_list_clusters_failure_is_recorded_and_gathering_continues(self):
        w = MagicMock()
        client = _client(list_clusters_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ecs_cluster' for c in w.add_error.call_args_list)

    def test_adds_one_resource_per_task_definition_family_with_redacted_env(self):
        w = MagicMock()
        task_def = {'taskDefinitionArn': 'arn:aws:ecs:us-east-1:1:task-definition/my-task:3', 'revision': 3,
                    'containerDefinitions': [{'environment': [{'name': 'X', 'value': 'y'}]}]}
        client = _client(families=['my-task'], task_def_by_family={'my-task': task_def})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        task_call = calls['ecs_task_definition']
        assert task_call.kwargs['resource_id'] == 'arn:aws:ecs:us-east-1:1:task-definition/my-task:3'
        assert task_call.kwargs['resource_name'] == 'my-task:3'
        assert task_call.kwargs['raw']['containerDefinitions'][0]['environment'] == ['X']

    def test_a_task_definition_describe_failure_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'taskDefinitionArn': 'arn:good', 'revision': 1, 'containerDefinitions': []}
        client = _client(families=['bad', 'good'], task_def_by_family={'good': good}, task_def_error_families={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ecs_task_definition:bad' for c in w.add_error.call_args_list)
        task_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'ecs_task_definition']
        assert len(task_calls) == 1

    def test_a_list_families_failure_is_recorded_and_does_not_abort_clusters(self):
        w = MagicMock()
        cluster = {'clusterArn': 'arn:aws:ecs:us-east-1:1:cluster/c1'}
        client = _client(cluster_arns=['arn:aws:ecs:us-east-1:1:cluster/c1'], clusters=[cluster], list_families_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'ecs_task_definition' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'ecs_cluster' in calls
