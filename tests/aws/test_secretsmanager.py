"""Unit tests for lensix_inventory.aws.secretsmanager — Secrets Manager secrets."""

from unittest.mock import MagicMock, patch

import lensix_inventory.aws.secretsmanager as m


def _sm_client(pages):
    client = MagicMock()
    client.list_secrets.side_effect = pages
    return client


class TestGetSecrets:
    def test_paginates_via_next_token(self):
        client = _sm_client([
            {'SecretList': [{'Name': 's1'}], 'NextToken': 'tok'},
            {'SecretList': [{'Name': 's2'}]},
        ])
        with patch.object(m.boto3, 'client', return_value=client):
            secrets = m.get_secrets('us-east-1')
        assert [s['Name'] for s in secrets] == ['s1', 's2']

    def test_no_secrets(self):
        client = _sm_client([{'SecretList': []}])
        with patch.object(m.boto3, 'client', return_value=client):
            assert m.get_secrets('us-east-1') == []


class TestGather:
    def test_adds_one_resource_per_secret(self):
        w = MagicMock()
        secret = {'ARN': 'arn:aws:secretsmanager:us-east-1:1:secret:s1', 'Name': 's1'}
        client = _sm_client([{'SecretList': [secret]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        w.add_resource.assert_called_once_with(
            resource_type='secretsmanager_secret', region='us-east-1',
            resource_id='arn:aws:secretsmanager:us-east-1:1:secret:s1', resource_name='s1', raw=secret, tags=None,
        )

    def test_secret_tags_are_passed_through_for_suppression(self):
        w = MagicMock()
        secret = {'ARN': 'arn:1', 'Name': 's1', 'Tags': [{'Key': 'lensix-suppress', 'Value': 'true'}]}
        client = _sm_client([{'SecretList': [secret]}])
        with patch.object(m.boto3, 'client', return_value=client):
            m.gather('us-east-1', w)
        assert w.add_resource.call_args.kwargs['tags'] == secret['Tags']
