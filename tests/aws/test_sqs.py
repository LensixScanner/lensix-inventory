"""Unit tests for lensix_inventory.aws.sqs — SQS queues."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.sqs as m


def _sqs_client(urls, attrs_by_url=None, attrs_error_urls=None, tags_by_url=None):
    client = MagicMock()
    client.get_paginator.return_value.paginate.return_value = [{'QueueUrls': urls}]
    attrs_by_url = attrs_by_url or {}
    attrs_error_urls = attrs_error_urls or set()
    tags_by_url = tags_by_url or {}
    client.list_queue_tags.side_effect = lambda QueueUrl: {'Tags': tags_by_url.get(QueueUrl, {})}

    def _get_attrs(QueueUrl, AttributeNames):
        if QueueUrl in attrs_error_urls:
            raise RuntimeError('boom')
        return {'Attributes': attrs_by_url[QueueUrl]}
    client.get_queue_attributes.side_effect = _get_attrs
    return client


class TestGather:
    def test_adds_one_resource_keyed_by_the_resolved_queue_arn(self):
        w = MagicMock()
        url = 'https://sqs.us-east-1.amazonaws.com/123456789012/my-queue'
        attrs = {'QueueArn': 'arn:aws:sqs:us-east-1:123456789012:my-queue'}
        client = _sqs_client([url], attrs_by_url={url: attrs})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='sqs_queue', region='us-east-1',
            resource_id='arn:aws:sqs:us-east-1:123456789012:my-queue', resource_name='my-queue', raw=attrs,
            tags={},
        )

    def test_queue_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        url = 'https://sqs.us-east-1.amazonaws.com/123456789012/my-queue'
        client = _sqs_client([url], attrs_by_url={url: {}}, tags_by_url={url: {'lensix-suppress': 'true'}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == {'lensix-suppress': 'true'}

    def test_falls_back_to_the_url_when_arn_missing_from_attributes(self):
        w = MagicMock()
        url = 'https://sqs.us-east-1.amazonaws.com/123456789012/my-queue'
        client = _sqs_client([url], attrs_by_url={url: {}})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        _, kwargs = w.add_resource.call_args
        assert kwargs['resource_id'] == url

    def test_an_attributes_failure_for_one_queue_does_not_abort_the_others(self):
        w = MagicMock()
        bad_url = 'https://sqs.us-east-1.amazonaws.com/123456789012/bad'
        good_url = 'https://sqs.us-east-1.amazonaws.com/123456789012/good'
        client = _sqs_client([bad_url, good_url], attrs_by_url={good_url: {'QueueArn': 'arn:good'}}, attrs_error_urls={bad_url})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == f'sqs_queue:{bad_url}'
        w.add_resource.assert_called_once()

    def test_no_queues_gathers_nothing(self):
        w = MagicMock()
        client = _sqs_client([])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_not_called()
