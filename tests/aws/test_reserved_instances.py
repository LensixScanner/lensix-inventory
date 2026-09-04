"""Unit tests for lensix_inventory.aws.reserved_instances — Reserved
Instance / reserved-node holdings across EC2, RDS, ElastiCache, Redshift,
and Elasticsearch/OpenSearch."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.reserved_instances as m


def _paginated_client(output_key, pages):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{output_key: p} for p in pages]
    return client


class TestGetEc2ReservedInstances:
    def test_returns_reserved_instances_from_a_single_unpaginated_call(self):
        client = MagicMock()
        client.describe_reserved_instances.return_value = {'ReservedInstances': [{'ReservedInstancesId': 'ri-1'}]}
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_ec2_reserved_instances('us-east-1')
        assert result == [{'ReservedInstancesId': 'ri-1'}]

    def test_missing_key_returns_empty_list(self):
        client = MagicMock()
        client.describe_reserved_instances.return_value = {}
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_ec2_reserved_instances('us-east-1') == []


class TestGetRdsReservedInstances:
    def test_paginates_across_pages(self):
        client = _paginated_client('ReservedDBInstances', [[{'ReservedDBInstanceId': 'a'}], [{'ReservedDBInstanceId': 'b'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_rds_reserved_instances('us-east-1')
        assert [r['ReservedDBInstanceId'] for r in result] == ['a', 'b']


class TestGetElasticacheReservedNodes:
    def test_paginates_across_pages(self):
        client = _paginated_client('ReservedCacheNodes', [[{'ReservedCacheNodeId': 'a'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_elasticache_reserved_nodes('us-east-1')
        assert [r['ReservedCacheNodeId'] for r in result] == ['a']


class TestGetRedshiftReservedNodes:
    def test_paginates_across_pages(self):
        client = _paginated_client('ReservedNodes', [[{'ReservedNodeId': 'a'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_redshift_reserved_nodes('us-east-1')
        assert [r['ReservedNodeId'] for r in result] == ['a']


class TestGetElasticsearchReservedInstances:
    def test_paginates_across_pages(self):
        client = _paginated_client('ReservedElasticsearchInstances', [[{'ReservedElasticsearchInstanceId': 'a'}]])
        with patch.object(m.boto3, 'client', return_value=client):
            result = m.get_elasticsearch_reserved_instances('us-east-1')
        assert [r['ReservedElasticsearchInstanceId'] for r in result] == ['a']


def _multi_service_client(ec2=None, rds=None, elasticache=None, redshift=None, es=None, raise_for=frozenset()):
    """One boto3.client(...) stand-in dispatching by service name — mirrors
    how gather() actually reaches AWS (each fetcher makes its own
    boto3.client() call), rather than patching the fetch functions
    themselves (which gather() calls via the module-level _SOURCES list,
    captured at import time — patching the functions after the fact
    wouldn't be seen by that already-bound list)."""
    by_service = {
        'ec2': (ec2 or [], 'describe_reserved_instances', 'ReservedInstances'),
        'rds': (rds or [], 'describe_reserved_db_instances', 'ReservedDBInstances'),
        'elasticache': (elasticache or [], 'describe_reserved_cache_nodes', 'ReservedCacheNodes'),
        'redshift': (redshift or [], 'describe_reserved_nodes', 'ReservedNodes'),
        'es': (es or [], 'describe_reserved_elasticsearch_instances', 'ReservedElasticsearchInstances'),
    }

    def _make_client(service_name, **kwargs):
        records, op, key = by_service[service_name]
        client = MagicMock()
        if service_name in raise_for:
            getattr(client, op).side_effect = RuntimeError('boom')
            client.get_paginator.side_effect = RuntimeError('boom')
        elif service_name == 'ec2':
            client.describe_reserved_instances.return_value = {key: records}
        else:
            client.get_paginator.return_value.paginate.return_value = [{key: records}]
        return client
    return _make_client


class TestGather:
    def test_ec2_reservation_uses_its_id_and_tags(self):
        w = MagicMock()
        client_fn = _multi_service_client(ec2=[{'ReservedInstancesId': 'ri-1', 'Tags': [{'Key': 'a', 'Value': 'b'}]}])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once()
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'ec2_reserved_instance'
        assert kwargs['resource_id'] == 'ri-1'
        assert kwargs['resource_name'] == 'ri-1'
        assert kwargs['tags'] == [{'Key': 'a', 'Value': 'b'}]

    def test_rds_reservation_prefers_its_arn_as_resource_id(self):
        w = MagicMock()
        rds_record = {'ReservedDBInstanceId': 'ri-rds-1', 'ReservedDBInstanceArn': 'arn:aws:rds:...:ri-rds-1'}
        client_fn = _multi_service_client(rds=[rds_record])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'rds_reserved_instance'
        assert kwargs['resource_id'] == 'arn:aws:rds:...:ri-rds-1'
        assert kwargs['resource_name'] == 'ri-rds-1'
        assert kwargs['tags'] is None

    def test_elasticache_reservation_prefers_its_arn_as_resource_id(self):
        w = MagicMock()
        record = {'ReservedCacheNodeId': 'ri-ec-1', 'ReservationARN': 'arn:aws:elasticache:...:ri-ec-1'}
        client_fn = _multi_service_client(elasticache=[record])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'elasticache_reserved_instance'
        assert kwargs['resource_id'] == 'arn:aws:elasticache:...:ri-ec-1'
        assert kwargs['tags'] is None

    def test_redshift_reservation_has_no_arn_so_falls_back_to_its_id(self):
        w = MagicMock()
        client_fn = _multi_service_client(redshift=[{'ReservedNodeId': 'ri-rs-1'}])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'redshift_reserved_node'
        assert kwargs['resource_id'] == 'ri-rs-1'
        assert kwargs['tags'] is None

    def test_elasticsearch_reservation_falls_back_to_id_when_reservation_name_is_blank(self):
        w = MagicMock()
        client_fn = _multi_service_client(es=[{'ReservedElasticsearchInstanceId': 'ri-es-1', 'ReservationName': ''}])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_type'] == 'elasticsearch_reserved_instance'
        assert kwargs['resource_id'] == 'ri-es-1'
        assert kwargs['resource_name'] == 'ri-es-1'

    def test_elasticsearch_reservation_uses_its_name_when_set(self):
        w = MagicMock()
        client_fn = _multi_service_client(es=[{'ReservedElasticsearchInstanceId': 'ri-es-1', 'ReservationName': 'my-reservation'}])
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        kwargs = w.add_resource.call_args.kwargs
        assert kwargs['resource_name'] == 'my-reservation'

    def test_one_services_failure_is_isolated_and_the_others_still_gather(self):
        w = MagicMock()
        client_fn = _multi_service_client(ec2=[{'ReservedInstancesId': 'ri-1'}], raise_for={'rds'})
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        w.add_error.assert_called_once()
        assert 'rds_reserved_instance' in w.add_error.call_args.kwargs['source']
        w.add_resource.assert_called_once()
        assert w.add_resource.call_args.kwargs['resource_type'] == 'ec2_reserved_instance'

    def test_no_reservations_anywhere_gathers_nothing(self):
        w = MagicMock()
        client_fn = _multi_service_client()
        with patch.object(m.boto3, 'client', side_effect=client_fn):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
        w.add_error.assert_not_called()
