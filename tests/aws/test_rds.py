"""Unit tests for lensix_inventory.aws.rds — DB instances, DB clusters,
and manual DB snapshots."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.rds as m


def _client(instances=None, clusters=None, ssl_param_by_pg=None, ssl_param_raises_pgs=None,
            clusters_raise=False, snapshots=None, snapshots_raise=False, connection_datapoints=None,
            engine_versions_by_engine=None, engine_versions_raise_engines=None):
    client = MagicMock()

    ssl_param_by_pg = ssl_param_by_pg or {}
    ssl_param_raises_pgs = ssl_param_raises_pgs or set()
    engine_versions_by_engine = engine_versions_by_engine or {}
    engine_versions_raise_engines = engine_versions_raise_engines or set()
    if connection_datapoints is not None:
        client.get_metric_statistics.return_value = {'Datapoints': connection_datapoints}

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'describe_db_instances':
            p.paginate.return_value = [{'DBInstances': instances or []}]
        elif op_name == 'describe_db_clusters':
            if clusters_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'DBClusters': clusters or []}]
        elif op_name == 'describe_db_snapshots':
            if snapshots_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'DBSnapshots': snapshots or []}]
        elif op_name == 'describe_db_engine_versions':
            def _paginate(Engine):
                if Engine in engine_versions_raise_engines:
                    raise RuntimeError('boom')
                versions = [{'EngineVersion': v} for v in engine_versions_by_engine.get(Engine, [])]
                return [{'DBEngineVersions': versions}]
            p.paginate.side_effect = _paginate
        return p
    client.get_paginator.side_effect = _get_paginator

    def _describe_params(DBParameterGroupName, Filters):
        if DBParameterGroupName in ssl_param_raises_pgs:
            raise RuntimeError('boom')
        return {'Parameters': ssl_param_by_pg.get(DBParameterGroupName, [])}
    client.describe_db_parameters.side_effect = _describe_params
    return client


class TestGetSslParameter:
    def test_returns_the_matching_parameter_for_postgres(self):
        instance = {'Engine': 'postgres', 'DBParameterGroups': [{'DBParameterGroupName': 'pg-1'}]}
        client = _client(ssl_param_by_pg={'pg-1': [{'ParameterName': 'rds.force_ssl', 'ParameterValue': '1'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            param = m.get_ssl_parameter('us-east-1', instance)
        assert param == {'ParameterName': 'rds.force_ssl', 'ParameterValue': '1'}

    def test_uses_the_mysql_parameter_name_for_non_postgres_engines(self):
        instance = {'Engine': 'mysql', 'DBParameterGroups': [{'DBParameterGroupName': 'pg-1'}]}
        client = _client(ssl_param_by_pg={'pg-1': [{'ParameterName': 'require_secure_transport', 'ParameterValue': 'ON'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            param = m.get_ssl_parameter('us-east-1', instance)
        assert param['ParameterName'] == 'require_secure_transport'

    def test_returns_none_without_a_parameter_group(self):
        instance = {'Engine': 'mysql'}
        assert m.get_ssl_parameter('us-east-1', instance) is None

    def test_returns_none_on_failure(self):
        instance = {'Engine': 'mysql', 'DBParameterGroups': [{'DBParameterGroupName': 'pg-1'}]}
        client = _client(ssl_param_raises_pgs={'pg-1'})
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_ssl_parameter('us-east-1', instance) is None


class TestGather:
    def test_adds_one_resource_per_instance_with_ssl_parameter_and_scope_id(self):
        w = MagicMock()
        instance = {
            'DBInstanceIdentifier': 'db1', 'Engine': 'postgres',
            'DBParameterGroups': [{'DBParameterGroupName': 'pg-1'}],
            'DBSubnetGroup': {'VpcId': 'vpc-1'},
        }
        client = _client(instances=[instance], ssl_param_by_pg={'pg-1': [{'ParameterName': 'rds.force_ssl', 'ParameterValue': '1'}]})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        instance_call = calls['rds_instance']
        assert instance_call.kwargs['resource_id'] == 'db1'
        assert instance_call.kwargs['scope_id'] == 'vpc-1'
        assert instance_call.kwargs['raw']['_SSLParameter']['ParameterName'] == 'rds.force_ssl'

    def test_instance_tags_are_passed_through_for_suppression(self):
        # RDS uses TagList, not Tags, across every describe_db_* API.
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1', 'TagList': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        client = _client(instances=[instance])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['tags'] == instance['TagList']

    def test_adds_one_resource_per_cluster_without_a_scope_id(self):
        w = MagicMock()
        cluster = {'DBClusterIdentifier': 'c1'}
        client = _client(clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_cluster'].kwargs['resource_id'] == 'c1'
        assert 'scope_id' not in calls['rds_cluster'].kwargs

    def test_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = {'DBClusterIdentifier': 'c1', 'TagList': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        client = _client(clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_cluster'].kwargs['tags'] == cluster['TagList']

    def test_a_clusters_failure_does_not_prevent_instances_from_being_gathered(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1'}
        client = _client(instances=[instance], clusters_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'rds (clusters)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'rds_instance' in calls

    def test_a_db_instances_failure_is_not_caught_and_propagates(self):
        # Unlike the clusters fetch, get_db_instances() isn't wrapped in
        # its own try/except — a failure here propagates up to the
        # per-module isolation the orchestrator (__init__.py's run())
        # provides instead.
        w = MagicMock()
        client = MagicMock()
        client.get_paginator.return_value.paginate.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            try:
                m.gather('us-east-1', w)
                assert False, 'expected the instances failure to propagate'
            except RuntimeError:
                pass

    def test_the_original_instance_dict_is_not_mutated(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1'}
        client = _client(instances=[instance])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert '_SSLParameter' not in instance

    def test_adds_one_resource_per_manual_snapshot(self):
        w = MagicMock()
        snap = {'DBSnapshotIdentifier': 'snap-1', 'Encrypted': False}
        client = _client(snapshots=[snap])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        snap_call = calls['rds_snapshot']
        assert snap_call.kwargs['resource_id'] == 'snap-1'
        assert snap_call.kwargs['resource_name'] == 'snap-1'
        assert snap_call.kwargs['raw'] == snap

    def test_snapshot_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        snap = {'DBSnapshotIdentifier': 'snap-1', 'TagList': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        client = _client(snapshots=[snap])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_snapshot'].kwargs['tags'] == snap['TagList']

    def test_a_snapshots_failure_does_not_prevent_instances_or_clusters(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1'}
        cluster = {'DBClusterIdentifier': 'cluster1'}
        client = _client(instances=[instance], clusters=[cluster], snapshots_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'rds (snapshots)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'rds_instance' in calls
        assert 'rds_cluster' in calls
        assert 'rds_snapshot' not in calls

    def test_no_manual_snapshots_gathers_nothing_for_them(self):
        w = MagicMock()
        client = _client()
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        types = {c.kwargs['resource_type'] for c in w.add_resource.call_args_list}
        assert 'rds_snapshot' not in types

    def test_an_available_instance_gets_connection_datapoints_merged_in(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1', 'DBInstanceStatus': 'available'}
        datapoints = [{'Maximum': 0}] * 7
        client = _client(instances=[instance], connection_datapoints=datapoints)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['raw']['_ConnectionDatapoints'] == datapoints

    def test_a_non_available_instance_gets_no_connections_fetch_at_all(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1', 'DBInstanceStatus': 'stopped'}
        client = _client(instances=[instance])
        with patch.object(m, 'get_db_connections_7d') as get_connections:
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        get_connections.assert_not_called()
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['raw']['_ConnectionDatapoints'] is None

    def test_a_connections_fetch_failure_records_none_and_an_error(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1', 'DBInstanceStatus': 'available'}
        client = _client(instances=[instance])
        with patch.object(m, 'get_db_connections_7d', side_effect=RuntimeError('boom')):
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'rds (connections:db1)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['raw']['_ConnectionDatapoints'] is None

    def test_latest_major_versions_are_merged_in_per_engine(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1', 'Engine': 'mysql'}
        client = _client(instances=[instance], engine_versions_by_engine={'mysql': ['5.7.44', '8.0.35']})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['raw']['_LatestMajorVersions'] == [5, 8]

    def test_the_engine_lookup_only_happens_once_for_multiple_instances_sharing_it(self):
        w = MagicMock()
        instances = [
            {'DBInstanceIdentifier': 'db1', 'Engine': 'mysql'},
            {'DBInstanceIdentifier': 'db2', 'Engine': 'mysql'},
        ]
        client = _client(instances=instances, engine_versions_by_engine={'mysql': ['8.0.35']})
        with patch.object(m, 'get_latest_major_versions', wraps=m.get_latest_major_versions) as get_majors:
            with patch.object(m.boto3, 'client', return_value=client):
                m.gather('us-east-1', w)
        get_majors.assert_called_once_with('us-east-1', 'mysql')

    def test_an_engine_lookup_failure_records_none_and_an_error_but_does_not_abort_other_engines(self):
        w = MagicMock()
        instances = [
            {'DBInstanceIdentifier': 'db1', 'Engine': 'mysql'},
            {'DBInstanceIdentifier': 'db2', 'Engine': 'postgres'},
        ]
        client = _client(
            instances=instances,
            engine_versions_by_engine={'postgres': ['15.4']},
            engine_versions_raise_engines={'mysql'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'rds (latest engine versions:mysql)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_id']: c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'rds_instance'}
        assert calls['db1'].kwargs['raw']['_LatestMajorVersions'] is None
        assert calls['db2'].kwargs['raw']['_LatestMajorVersions'] == [15]

    def test_an_instance_without_an_engine_field_gets_none(self):
        w = MagicMock()
        instance = {'DBInstanceIdentifier': 'db1'}
        client = _client(instances=[instance])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['rds_instance'].kwargs['raw']['_LatestMajorVersions'] is None


class TestGetLatestMajorVersions:
    def test_returns_sorted_unique_majors(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{'DBEngineVersions': [
            {'EngineVersion': '8.0.35'}, {'EngineVersion': '5.7.44'}, {'EngineVersion': '8.0.28'},
        ]}]
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_latest_major_versions('us-east-1', 'mysql') == [5, 8]

    def test_scopes_the_paginate_call_to_the_given_engine(self):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value = [{'DBEngineVersions': []}]
        with patch.object(m.boto3, 'client', return_value=client):
            m.get_latest_major_versions('us-east-1', 'postgres')
        client.get_paginator.return_value.paginate.assert_called_once_with(Engine='postgres')


class TestGetDbConnections7d:
    def test_returns_the_datapoints_scoped_to_this_instance(self):
        datapoints = [{'Maximum': 1.0}, {'Maximum': 0.0}]
        client = MagicMock()
        client.get_metric_statistics.return_value = {'Datapoints': datapoints}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_db_connections_7d('us-east-1', 'db1') == datapoints
        call_kwargs = client.get_metric_statistics.call_args.kwargs
        assert call_kwargs['Dimensions'] == [{'Name': 'DBInstanceIdentifier', 'Value': 'db1'}]
        assert call_kwargs['MetricName'] == 'DatabaseConnections'
