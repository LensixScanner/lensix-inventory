"""Unit tests for lensix_inventory.aws.dynamodb — DynamoDB tables and DAX clusters."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.dynamodb as m


def _client(table_names=None, table_detail_by_name=None, table_error_names=None,
            backups_by_name=None, dax_clusters=None, tables_raise=False, dax_raise=False,
            table_tags_by_arn=None, dax_tags_by_arn=None):
    # list_tags_of_resource/list_tags below both need an explicit, real
    # {'Tags': [...]} response — an unconfigured MagicMock's own .get()
    # call returns another (always-truthy) MagicMock, which would make
    # get_table_tags()/get_dax_cluster_tags()'s own `while True: ...
    # NextToken` pagination loop spin forever (same trap noted in
    # test_sg.py's own _ec2_client_split docstring).
    client = MagicMock()
    table_names = table_names or []
    table_detail_by_name = table_detail_by_name or {}
    table_error_names = table_error_names or set()
    backups_by_name = backups_by_name or {}
    table_tags_by_arn = table_tags_by_arn or {}
    dax_tags_by_arn = dax_tags_by_arn or {}
    client.list_tags_of_resource.side_effect = lambda ResourceArn: {'Tags': table_tags_by_arn.get(ResourceArn, [])}
    client.list_tags.side_effect = lambda ResourceName: {'Tags': dax_tags_by_arn.get(ResourceName, [])}

    def _get_paginator(op_name):
        p = MagicMock()
        if op_name == 'list_tables':
            if tables_raise:
                p.paginate.side_effect = RuntimeError('boom')
            else:
                p.paginate.return_value = [{'TableNames': table_names}]
        return p
    client.get_paginator.side_effect = _get_paginator

    def _describe_table(TableName):
        if TableName in table_error_names:
            raise RuntimeError('boom')
        return {'Table': table_detail_by_name[TableName]}
    client.describe_table.side_effect = _describe_table

    def _backups(TableName):
        return {'ContinuousBackupsDescription': backups_by_name.get(TableName)}
    client.describe_continuous_backups.side_effect = _backups

    if dax_raise:
        client.describe_clusters.side_effect = RuntimeError('boom')
    else:
        client.describe_clusters.side_effect = [{'Clusters': dax_clusters or []}]
    return client


class TestGetTableTags:
    def test_paginates_across_multiple_pages(self):
        client = MagicMock()
        client.list_tags_of_resource.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'NextToken': 'tok'},
            {'Tags': [{'Key': 'b', 'Value': '2'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            tags = m.get_table_tags('us-east-1', 'arn:1')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]

    def test_a_failure_returns_an_empty_list(self):
        client = MagicMock()
        client.list_tags_of_resource.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_table_tags('us-east-1', 'arn:1') == []


class TestGetDaxClusterTags:
    def test_paginates_across_multiple_pages(self):
        client = MagicMock()
        client.list_tags.side_effect = [
            {'Tags': [{'Key': 'a', 'Value': '1'}], 'NextToken': 'tok'},
            {'Tags': [{'Key': 'b', 'Value': '2'}]},
        ]
        with patch.object(m.boto3, 'client', return_value=client):
            tags = m.get_dax_cluster_tags('us-east-1', 'arn:1')
        assert tags == [{'Key': 'a', 'Value': '1'}, {'Key': 'b', 'Value': '2'}]

    def test_a_failure_returns_an_empty_list(self):
        client = MagicMock()
        client.list_tags.side_effect = RuntimeError('boom')
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_dax_cluster_tags('us-east-1', 'arn:1') == []


class TestGather:
    def test_adds_one_resource_per_table_with_backups_merged_in(self):
        w = MagicMock()
        table = {'TableName': 't1', 'TableArn': 'arn:aws:dynamodb:us-east-1:1:table/t1'}
        client = _client(table_names=['t1'], table_detail_by_name={'t1': table}, backups_by_name={'t1': {'PointInTimeRecoveryStatus': 'ENABLED'}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        table_call = calls['dynamodb_table']
        assert table_call.kwargs['resource_id'] == 'arn:aws:dynamodb:us-east-1:1:table/t1'
        assert table_call.kwargs['raw']['_ContinuousBackups'] == {'PointInTimeRecoveryStatus': 'ENABLED'}

    def test_a_describe_table_failure_does_not_abort_the_others(self):
        w = MagicMock()
        good = {'TableName': 'good', 'TableArn': 'arn:good'}
        client = _client(table_names=['bad', 'good'], table_detail_by_name={'good': good}, table_error_names={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'dynamodb_table:bad' for c in w.add_error.call_args_list)
        table_calls = [c for c in w.add_resource.call_args_list if c.kwargs['resource_type'] == 'dynamodb_table']
        assert len(table_calls) == 1

    def test_table_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        table = {'TableName': 't1', 'TableArn': 'arn:1'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(table_names=['t1'], table_detail_by_name={'t1': table}, table_tags_by_arn={'arn:1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['dynamodb_table'].kwargs['tags'] == tags

    def test_adds_one_resource_per_dax_cluster(self):
        w = MagicMock()
        cluster = {'ClusterName': 'cache1', 'ClusterArn': 'arn:aws:dax:us-east-1:1:cache/cache1'}
        client = _client(dax_clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['dax_cluster'].kwargs['resource_id'] == 'arn:aws:dax:us-east-1:1:cache/cache1'

    def test_dax_cluster_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        cluster = {'ClusterName': 'cache1', 'ClusterArn': 'arn:aws:dax:us-east-1:1:cache/cache1'}
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        client = _client(dax_clusters=[cluster], dax_tags_by_arn={'arn:aws:dax:us-east-1:1:cache/cache1': tags})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert calls['dax_cluster'].kwargs['tags'] == tags

    def test_a_tables_failure_does_not_prevent_dax_clusters_from_being_gathered(self):
        w = MagicMock()
        cluster = {'ClusterName': 'cache1'}
        client = _client(tables_raise=True, dax_clusters=[cluster])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'dynamodb (tables)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'dax_cluster' in calls

    def test_a_dax_failure_does_not_prevent_tables_from_being_gathered(self):
        w = MagicMock()
        table = {'TableName': 't1', 'TableArn': 'arn:1'}
        client = _client(table_names=['t1'], table_detail_by_name={'t1': table}, dax_raise=True)
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert any(c.kwargs['source'] == 'dynamodb (dax clusters)' for c in w.add_error.call_args_list)
        calls = {c.kwargs['resource_type']: c for c in w.add_resource.call_args_list}
        assert 'dynamodb_table' in calls

    def test_the_original_table_dict_is_not_mutated_beyond_backups_being_added(self):
        # get_table_detail returns a fresh dict from the API each call in
        # practice; this just confirms the mutation happens on that dict,
        # not shared global state.
        w = MagicMock()
        table = {'TableName': 't1', 'TableArn': 'arn:1'}
        client = _client(table_names=['t1'], table_detail_by_name={'t1': table})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert table['_ContinuousBackups'] is None
