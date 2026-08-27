"""Unit tests for lensix_inventory.aws.sns — SNS topics."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.sns as m


def _sns_client(topics, attrs_by_arn=None, attrs_error_arns=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'Topics': [{'TopicArn': t} for t in topics]}]
    attrs_by_arn = attrs_by_arn or {}
    attrs_error_arns = attrs_error_arns or set()

    def _get_attrs(TopicArn):
        if TopicArn in attrs_error_arns:
            raise RuntimeError('boom')
        return {'Attributes': attrs_by_arn[TopicArn]}
    client.get_topic_attributes.side_effect = _get_attrs
    return client


class TestGather:
    def test_adds_one_resource_per_topic_named_from_the_arn_suffix(self):
        w = MagicMock()
        attrs = {'Policy': '{}'}
        client = _sns_client(['arn:aws:sns:us-east-1:1:my-topic'], attrs_by_arn={'arn:aws:sns:us-east-1:1:my-topic': attrs})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='sns_topic', region='us-east-1',
            resource_id='arn:aws:sns:us-east-1:1:my-topic', resource_name='my-topic', raw=attrs,
        )

    def test_an_attributes_failure_for_one_topic_does_not_abort_the_others(self):
        w = MagicMock()
        client = _sns_client(
            ['arn:bad', 'arn:good'],
            attrs_by_arn={'arn:good': {}},
            attrs_error_arns={'arn:bad'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'sns_topic:arn:bad'
        w.add_resource.assert_called_once()

    def test_no_topics_gathers_nothing(self):
        w = MagicMock()
        client = _sns_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
