"""Unit tests for lensix_inventory.aws.mq — Amazon MQ brokers."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.mq as m


def _mq_client(pages, detail_by_id=None, detail_error_ids=None):
    client = MagicMock()
    client.list_brokers.side_effect = pages
    detail_by_id = detail_by_id or {}
    detail_error_ids = detail_error_ids or set()

    def _describe(BrokerId):
        if BrokerId in detail_error_ids:
            raise RuntimeError('boom')
        return detail_by_id[BrokerId]
    client.describe_broker.side_effect = _describe
    return client


class TestGetBrokers:
    def test_paginates_via_next_token(self):
        client = _mq_client([
            {'BrokerSummaries': [{'BrokerId': 'b1'}], 'NextToken': 'tok'},
            {'BrokerSummaries': [{'BrokerId': 'b2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            brokers = m.get_brokers('us-east-1')
        assert [b['BrokerId'] for b in brokers] == ['b1', 'b2']


class TestGather:
    def test_adds_one_resource_per_broker_using_the_describe_result(self):
        w = MagicMock()
        summary = {'BrokerId': 'b1', 'BrokerName': 'my-broker'}
        detail = {'BrokerArn': 'arn:aws:mq:us-east-1:1:broker:b1', 'BrokerName': 'my-broker'}
        client = _mq_client([{'BrokerSummaries': [summary]}], detail_by_id={'b1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='mq_broker', region='us-east-1',
            resource_id='arn:aws:mq:us-east-1:1:broker:b1', resource_name='my-broker', raw=detail,
        )

    def test_falls_back_to_the_broker_id_when_arn_missing(self):
        w = MagicMock()
        summary = {'BrokerId': 'b1', 'BrokerName': 'my-broker'}
        client = _mq_client([{'BrokerSummaries': [summary]}], detail_by_id={'b1': {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == 'b1'

    def test_a_describe_failure_for_one_broker_does_not_abort_the_others(self):
        w = MagicMock()
        bad = {'BrokerId': 'bad', 'BrokerName': 'bad'}
        good = {'BrokerId': 'good', 'BrokerName': 'good'}
        client = _mq_client([{'BrokerSummaries': [bad, good]}], detail_by_id={'good': {}}, detail_error_ids={'bad'})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'mq_broker:bad'
        w.add_resource.assert_called_once()
