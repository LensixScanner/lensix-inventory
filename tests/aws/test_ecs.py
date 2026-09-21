"""Unit tests for lensix_inventory.aws.ecs — clusters and task definitions."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.ecs as m


def _client(cluster_arns=None, clusters=None, families=None, task_def_by_family=None,
            task_def_error_families=None, list_clusters_raise=False, describe_clusters_raise=False,
            list_families_raise=False, task_def_tags_by_family=None,
            service_arns_by_cluster=None, service_details=None, list_services_raise_for=None):
    client = MagicMock()
    service_arns_by_cluster = service_arns_by_cluster or {}
    service_details = service_details or {}
    list_services_raise_for = list_services_raise_for or set()

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
        elif op_name == 'list_services':
            def _paginate(cluster):
                if cluster in list_services_raise_for:
                    raise RuntimeError('boom')
                return [{'serviceArns': service_arns_by_cluster.get(cluster, [])}]
            p.paginate.side_effect = _paginate
        return p
    client.get_paginator.side_effect = _get_paginator

    if describe_clusters_raise:
        client.describe_clusters.side_effect = RuntimeError('boom')
    else:
        client.describe_clusters.return_value = {'clusters': clusters or []}

    client.describe_services.side_effect = lambda cluster, services: {
        'services': [service_details[s] for s in services if s in service_details]}

    task_def_by_family = task_def_by_family or {}
    task_def_error_families = task_def_error_families or set()
    task_def_tags_by_family = task_def_tags_by_family or {}

    def _describe_task_def(taskDefinition, include=None):
        if taskDefinition in task_def_error_families:
            raise RuntimeError('boom')
        return {'taskDefinition': task_def_by_family[taskDefinition],
                'tags': task_def_tags_by_family.get(taskDefinition, [])}
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

    def test_cluster_tags_are_passed_through_for_suppression(self):
        # ECS tags use lowercase {'key','value'} dicts, not EC2-family's
        # uppercase {'Key','Value'} — see _normalize_tags in
        # lensix_inventory.common.output.
        w = MagicMock()
        cluster = {'clusterArn': 'arn:1', 'tags': [{'key': 'lensix-suppress', 'value': 'true'}]}
        client = _client(cluster_arns=['arn:1'], clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecs_cluster'].kwargs['tags'] == cluster['tags']

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

    def test_task_definition_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        task_def = {'taskDefinitionArn': 'arn:1', 'revision': 1, 'containerDefinitions': []}
        tags = [{'key': 'lensix-suppress', 'value': 'true'}]
        client = _client(families=['my-task'], task_def_by_family={'my-task': task_def},
                          task_def_tags_by_family={'my-task': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecs_task_definition'].kwargs['tags'] == tags

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

    def test_services_are_fused_into_the_clusters_own_raw_record(self):
        w = MagicMock()
        cluster_arn = 'arn:aws:ecs:us-east-1:1:cluster/c1'
        cluster = {'clusterArn': cluster_arn}
        service = {'serviceArn': 'arn:svc-1', 'networkConfiguration': {'awsvpcConfiguration': {'subnets': ['subnet-1'], 'securityGroups': ['sg-1']}}}
        client = _client(cluster_arns=[cluster_arn], clusters=[cluster],
                          service_arns_by_cluster={cluster_arn: ['arn:svc-1']}, service_details={'arn:svc-1': service})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecs_cluster'].kwargs['raw']['_Services'] == [service]

    def test_a_cluster_with_no_services_gets_an_empty_list(self):
        w = MagicMock()
        cluster_arn = 'arn:1'
        cluster = {'clusterArn': cluster_arn}
        client = _client(cluster_arns=[cluster_arn], clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['ecs_cluster'].kwargs['raw']['_Services'] == []
        client.describe_services.assert_not_called()

    def test_an_awsvpc_service_produces_subnet_and_security_group_edges(self):
        w = MagicMock()
        cluster_arn = 'arn:1'
        cluster = {'clusterArn': cluster_arn}
        service = {'serviceArn': 'arn:svc-1', 'networkConfiguration': {'awsvpcConfiguration': {'subnets': ['subnet-1'], 'securityGroups': ['sg-1']}}}
        client = _client(cluster_arns=[cluster_arn], clusters=[cluster],
                          service_arns_by_cluster={cluster_arn: ['arn:svc-1']}, service_details={'arn:svc-1': service})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        edges = [c.kwargs for c in w.add_edge.call_args_list]
        assert {'from_type': 'ecs_cluster', 'from_id': cluster_arn, 'to_type': 'subnet', 'to_id': 'subnet-1', 'relationship': 'in_subnet'} in edges
        assert {'from_type': 'ecs_cluster', 'from_id': cluster_arn, 'to_type': 'security_group', 'to_id': 'sg-1', 'relationship': 'member_of_sg'} in edges

    def test_a_bridge_mode_service_produces_no_edges(self):
        w = MagicMock()
        cluster_arn = 'arn:1'
        cluster = {'clusterArn': cluster_arn}
        service = {'serviceArn': 'arn:svc-1', 'networkConfiguration': {}}
        client = _client(cluster_arns=[cluster_arn], clusters=[cluster],
                          service_arns_by_cluster={cluster_arn: ['arn:svc-1']}, service_details={'arn:svc-1': service})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_a_fully_suppressed_cluster_produces_no_edges(self):
        w = MagicMock()
        w.add_resource.return_value = False
        cluster_arn = 'arn:1'
        cluster = {'clusterArn': cluster_arn}
        service = {'serviceArn': 'arn:svc-1', 'networkConfiguration': {'awsvpcConfiguration': {'subnets': ['subnet-1']}}}
        client = _client(cluster_arns=[cluster_arn], clusters=[cluster],
                          service_arns_by_cluster={cluster_arn: ['arn:svc-1']}, service_details={'arn:svc-1': service})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_edge.assert_not_called()

    def test_a_list_services_failure_for_one_cluster_does_not_abort_the_others(self):
        w = MagicMock()
        bad_arn, good_arn = 'arn:bad', 'arn:good'
        service = {'serviceArn': 'arn:svc-1', 'networkConfiguration': {}}
        client = _client(cluster_arns=[bad_arn, good_arn], clusters=[{'clusterArn': bad_arn}, {'clusterArn': good_arn}],
                          service_arns_by_cluster={good_arn: ['arn:svc-1']}, service_details={'arn:svc-1': service},
                          list_services_raise_for={bad_arn})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == f'ecs_cluster:{bad_arn} (services)' for c in w.add_error.call_args_list)
        cluster_calls = {c.kwargs['resource_id']: c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'ecs_cluster'}
        assert cluster_calls[bad_arn].kwargs['raw']['_Services'] == []
        assert cluster_calls[good_arn].kwargs['raw']['_Services'] == [service]
