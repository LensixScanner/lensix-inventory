"""Unit tests for lensix_inventory.aws.transfer — Transfer Family servers."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.transfer as m


def _tf_client(pages, detail_by_id=None, detail_error_ids=None):
    client = MagicMock()
    client.list_servers.side_effect = pages
    detail_by_id = detail_by_id or {}
    detail_error_ids = detail_error_ids or set()

    def _describe(ServerId):
        if ServerId in detail_error_ids:
            raise RuntimeError('boom')
        return {'Server': detail_by_id[ServerId]}
    client.describe_server.side_effect = _describe
    return client


class TestServerName:
    def test_uses_the_name_tag_when_present(self):
        server = {'ServerId': 's-1', 'Tags': [{'Key': 'Name', 'Value': 'sftp-prod'}]}
        assert m._server_name(server) == 'sftp-prod'

    def test_falls_back_to_the_server_id_without_a_name_tag(self):
        server = {'ServerId': 's-1'}
        assert m._server_name(server) == 's-1'


class TestGetServers:
    def test_paginates_via_next_token(self):
        client = _tf_client([
            {'Servers': [{'ServerId': 's-1'}], 'NextToken': 'tok'},
            {'Servers': [{'ServerId': 's-2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_servers('us-east-1') == ['s-1', 's-2']


class TestGather:
    def test_adds_one_resource_per_server(self):
        w = MagicMock()
        detail = {'ServerId': 's-1', 'Tags': [{'Key': 'Name', 'Value': 'sftp-prod'}]}
        client = _tf_client([{'Servers': [{'ServerId': 's-1'}]}], detail_by_id={'s-1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='transfer_server', region='us-east-1',
            resource_id='s-1', resource_name='sftp-prod', raw=detail, tags=detail['Tags'],
        )

    def test_server_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        tags = [{'Key': 'lensix-suppress', 'Value': 'true'}]
        detail = {'ServerId': 's-1', 'Tags': tags}
        client = _tf_client([{'Servers': [{'ServerId': 's-1'}]}], detail_by_id={'s-1': detail})
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == tags

    def test_a_describe_failure_for_one_server_does_not_abort_the_others(self):
        w = MagicMock()
        client = _tf_client(
            [{'Servers': [{'ServerId': 'bad'}, {'ServerId': 'good'}]}],
            detail_by_id={'good': {'ServerId': 'good'}}, detail_error_ids={'bad'},
        )
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_error.call_count == 1
        assert w.add_error.call_args.kwargs['source'] == 'transfer_server:bad'
        w.add_resource.assert_called_once()
